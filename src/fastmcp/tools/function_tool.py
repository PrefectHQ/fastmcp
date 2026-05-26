"""Standalone @tool decorator for FastMCP."""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    Protocol,
    TypeVar,
    overload,
    runtime_checkable,
)

import anyio
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, Icon, ToolAnnotations
from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

import fastmcp
from fastmcp.decorators import resolve_task_config
from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.auth.authorization import AuthCheck
from fastmcp.server.dependencies import without_injected_parameters
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.base import (
    Tool,
    ToolResult,
    ToolResultSerializerType,
)
from fastmcp.tools.function_parsing import ParsedFunction, _is_object_schema
from fastmcp.utilities.async_utils import (
    call_sync_fn_in_threadpool,
    is_coroutine_function,
)
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    NotSet,
    NotSetT,
    get_cached_typeadapter,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

F = TypeVar("F", bound=Callable[..., Any])


@runtime_checkable
class DecoratedTool(Protocol):
    """Protocol for functions decorated with @tool."""

    __fastmcp__: ToolMeta

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, kw_only=True)
class ToolMeta:
    """Metadata attached to functions by the @tool decorator."""

    type: Literal["tool"] = field(default="tool", init=False)
    name: str | None = None
    version: str | int | None = None
    title: str | None = None
    description: str | None = None
    icons: list[Icon] | None = None
    tags: set[str] | None = None
    output_schema: dict[str, Any] | NotSetT | None = NotSet
    annotations: ToolAnnotations | None = None
    meta: dict[str, Any] | None = None
    app: Any = None
    task: bool | TaskConfig | None = None
    exclude_args: list[str] | None = None
    serializer: Any | None = None
    timeout: float | None = None
    auth: AuthCheck | list[AuthCheck] | None = None
    enabled: bool = True
    run_in_thread: bool = True


class FunctionTool(Tool):
    fn: SkipJsonSchema[Callable[..., Any]]
    return_type: Annotated[SkipJsonSchema[Any], Field(exclude=True)] = None
    run_in_thread: Annotated[
        bool,
        Field(
            description=(
                "Applies to sync tool functions only. When True (default), sync "
                "functions are dispatched to a worker thread so they don't block "
                "the event loop. Set to False to run the sync function inline on "
                "the event loop thread — useful for libraries with thread "
                "affinity (e.g. Windows COM, tkinter). Ignored for async functions, "
                "which always run on the event loop. Cannot be combined with "
                "`timeout` on a sync function: inline calls have no cancellation "
                "checkpoints, so the timeout would be a silent no-op."
            )
        ),
    ] = True

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        *,
        metadata: ToolMeta | None = None,
        # Keep individual params for backwards compat
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        exclude_args: list[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        serializer: ToolResultSerializerType | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        timeout: float | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        run_in_thread: bool | None = None,
    ) -> FunctionTool:
        """Create a FunctionTool from a function.

        Args:
            fn: The function to wrap
            metadata: ToolMeta object with all configuration. If provided,
                individual parameters must not be passed.
            name, title, etc.: Individual parameters for backwards compatibility.
                Cannot be used together with metadata parameter.
        """
        # Check mutual exclusion
        individual_params_provided = (
            any(
                x is not None and x is not NotSet
                for x in [
                    name,
                    version,
                    title,
                    description,
                    icons,
                    tags,
                    annotations,
                    meta,
                    task,
                    serializer,
                    timeout,
                    auth,
                    run_in_thread,
                ]
            )
            or output_schema is not NotSet
            or exclude_args is not None
        )

        if metadata is not None and individual_params_provided:
            raise TypeError(
                "Cannot pass both 'metadata' and individual parameters to from_function(). "
                "Use metadata alone or individual parameters alone."
            )

        # Build metadata from kwargs if not provided, but preserve @tool metadata
        if metadata is None:
            if hasattr(fn, "__fastmcp__"):
                metadata = fn.__fastmcp__
            else:
                metadata = ToolMeta(
                    name=name,
                    version=version,
                    title=title,
                    description=description,
                    icons=icons,
                    tags=tags,
                    output_schema=output_schema,
                    annotations=annotations,
                    meta=meta,
                    task=task,
                    exclude_args=exclude_args,
                    serializer=serializer,
                    timeout=timeout,
                    auth=auth,
                    run_in_thread=True if run_in_thread is None else run_in_thread,
                )

        if metadata.serializer is not None and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The `serializer` parameter is deprecated. "
                "Return ToolResult from your tools for full control over serialization. "
                "See https://gofastmcp.com/servers/tools#custom-serialization for migration examples.",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        if metadata.exclude_args and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The `exclude_args` parameter is deprecated as of FastMCP 2.14. "
                "Use dependency injection with `Depends()` instead for better lifecycle management. "
                "See https://gofastmcp.com/servers/dependency-injection#using-depends for examples.",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )

        parsed_fn = ParsedFunction.from_function(fn, exclude_args=metadata.exclude_args)
        func_name = metadata.name or parsed_fn.name

        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        if (
            metadata.timeout is not None
            and not metadata.run_in_thread
            and not is_coroutine_function(fn)
            and not inspect.isasyncgenfunction(fn)
        ):
            raise ValueError(
                f"Tool {func_name!r}: timeout cannot be enforced when "
                "run_in_thread=False on a sync function. Inline execution has "
                "no cancellation checkpoints, so the timeout would be a no-op. "
                "Either drop the timeout or remove run_in_thread=False and "
                "accept worker-thread dispatch."
            )

        # Normalize task to TaskConfig
        task_value = metadata.task
        if task_value is None:
            task_config = TaskConfig(mode="forbidden")
        elif isinstance(task_value, bool):
            task_config = TaskConfig.from_bool(task_value)
        else:
            task_config = task_value
        task_config.validate_function(fn, func_name)

        # Handle output_schema
        if isinstance(metadata.output_schema, NotSetT):
            final_output_schema = parsed_fn.output_schema
        else:
            final_output_schema = metadata.output_schema

        return cls(
            fn=fn,
            name=func_name,
            version=metadata.version,
            title=metadata.title,
            description=metadata.description,
            icons=metadata.icons,
            tags=metadata.tags,
            output_schema=final_output_schema,
            annotations=metadata.annotations,
            meta=metadata.meta,
            run_in_thread=metadata.run_in_thread,
        )

def tool(
    name_or_fn: str | Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    version: str | int | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[Icon] | None = None,
    tags: set[str] | None = None,
    output_schema: dict[str, Any] | NotSetT | None = NotSet,
    annotations: ToolAnnotations | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    task: bool | TaskConfig | None = None,
    exclude_args: list[str] | None = None,
    serializer: Any | None = None,
    timeout: float | None = None,
    auth: AuthCheck | list[AuthCheck] | None = None,
    run_in_thread: bool = True,
) -> Any:
    if isinstance(annotations, dict):
        annotations = ToolAnnotations(**annotations)

    def create_tool(fn: Callable[..., Any], tool_name: str | None) -> FunctionTool:
        tool_meta = ToolMeta(
            name=tool_name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            meta=meta,
            task=resolve_task_config(task),
            exclude_args=exclude_args,
            serializer=serializer,
            timeout=timeout,
            auth=auth,
            run_in_thread=run_in_thread,
        )
        return FunctionTool.from_function(fn, metadata=tool_meta)

    def attach_metadata(fn: F, tool_name: str | None) -> F:
        metadata = ToolMeta(
            name=tool_name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            meta=meta,
            task=task,
            exclude_args=exclude_args,
            serializer=serializer,
            timeout=timeout,
            auth=auth,
            run_in_thread=run_in_thread,
        )
        target = fn.__func__ if hasattr(fn, "__func__") else fn
        target.__fastmcp__ = metadata
        return fn

    def decorator(fn: F, tool_name: str | None) -> F:
        return attach_metadata(fn, tool_name)

    if inspect.isroutine(name_or_fn):
        return decorator(name_or_fn, name)
    elif isinstance(name_or_fn, str):
        if name is not None:
            raise TypeError("Cannot specify name both as first argument and keyword")
        tool_name = name_or_fn
    elif name_or_fn is None:
        tool_name = name
    else:
        raise TypeError(f"Invalid first argument: {type(name_or_fn).__name__}: {name_or_fn!r}")

    def wrapper(fn: F) -> F:
        return decorator(fn, tool_name)

    return wrapper
