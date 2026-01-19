"""Standalone @tool decorator for FastMCP."""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeVar,
    overload,
    runtime_checkable,
)

import anyio
import mcp.types
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, Icon, ToolAnnotations, ToolExecution

import fastmcp
from fastmcp.decorators import resolve_task_config
from fastmcp.server.dependencies import without_injected_parameters
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.function_parsing import ParsedFunction, _is_object_schema
from fastmcp.tools.tool import (
    AuthCheckCallable,
    Tool,
    ToolResult,
    ToolResultSerializerType,
)
from fastmcp.utilities.async_utils import call_sync_fn_in_threadpool
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
    task: bool | TaskConfig | None = None
    exclude_args: list[str] | None = None
    serializer: Any | None = None
    timeout: float | None = None
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None
    enabled: bool = True


class FunctionTool(Tool):
    fn: Callable[..., Any]

    def to_mcp_tool(
        self,
        **overrides: Any,
    ) -> mcp.types.Tool:
        """Convert the FastMCP tool to an MCP tool.

        Extends the base implementation to add task execution mode if enabled.
        """
        # Get base MCP tool from parent
        mcp_tool = super().to_mcp_tool(**overrides)

        # Add task execution mode per SEP-1686
        # Only set execution if not overridden and task execution is supported
        if self.task_config.supports_tasks() and "execution" not in overrides:
            mcp_tool.execution = ToolExecution(taskSupport=self.task_config.mode)

        return mcp_tool

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
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
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

        # Build metadata from kwargs if not provided
        if metadata is None:
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
            )

        if metadata.serializer is not None and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The `serializer` parameter is deprecated. "
                "Return ToolResult from your tools for full control over serialization. "
                "See https://gofastmcp.com/servers/tools#custom-serialization for migration examples.",
                DeprecationWarning,
                stacklevel=2,
            )
        if metadata.exclude_args and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The `exclude_args` parameter is deprecated as of FastMCP 2.14. "
                "Use dependency injection with `Depends()` instead for better lifecycle management. "
                "See https://gofastmcp.com/servers/dependencies for examples.",
                DeprecationWarning,
                stacklevel=2,
            )

        parsed_fn = ParsedFunction.from_function(fn, exclude_args=metadata.exclude_args)
        func_name = metadata.name or parsed_fn.name

        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

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

        if final_output_schema is not None and isinstance(final_output_schema, dict):
            if not _is_object_schema(final_output_schema):
                raise ValueError(
                    f"Output schemas must represent object types due to MCP spec limitations. "
                    f"Received: {final_output_schema!r}"
                )

        return cls(
            fn=parsed_fn.fn,
            name=metadata.name or parsed_fn.name,
            version=str(metadata.version) if metadata.version is not None else None,
            title=metadata.title,
            description=metadata.description or parsed_fn.description,
            icons=metadata.icons,
            parameters=parsed_fn.input_schema,
            output_schema=final_output_schema,
            annotations=metadata.annotations,
            tags=metadata.tags or set(),
            serializer=metadata.serializer,
            meta=metadata.meta,
            task_config=task_config,
            timeout=metadata.timeout,
            auth=metadata.auth,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Run the tool with arguments."""
        wrapper_fn = without_injected_parameters(self.fn)
        type_adapter = get_cached_typeadapter(wrapper_fn)

        # Apply timeout if configured
        if self.timeout is not None:
            try:
                with anyio.fail_after(self.timeout):
                    # Thread pool execution for sync functions, direct await for async
                    if inspect.iscoroutinefunction(wrapper_fn):
                        result = await type_adapter.validate_python(arguments)
                    else:
                        # Sync function: run in threadpool to avoid blocking
                        result = await call_sync_fn_in_threadpool(
                            type_adapter.validate_python, arguments
                        )
                        # Handle sync wrappers that return awaitables
                        if inspect.isawaitable(result):
                            result = await result
            except TimeoutError:
                logger.warning(
                    f"Tool '{self.name}' timed out after {self.timeout}s. "
                    f"Consider using task=True for long-running operations. "
                    f"See https://gofastmcp.com/servers/tasks"
                )
                raise McpError(
                    ErrorData(
                        code=-32000,
                        message=f"Tool '{self.name}' execution timed out after {self.timeout}s",
                    )
                ) from None
        else:
            # No timeout: use existing execution path
            if inspect.iscoroutinefunction(wrapper_fn):
                result = await type_adapter.validate_python(arguments)
            else:
                result = await call_sync_fn_in_threadpool(
                    type_adapter.validate_python, arguments
                )
                if inspect.isawaitable(result):
                    result = await result

        return self.convert_result(result)

    def register_with_docket(self, docket: Docket) -> None:
        """Register this tool with docket for background execution.

        FunctionTool registers the underlying function, which has the user's
        Depends parameters for docket to resolve.
        """
        if not self.task_config.supports_tasks():
            return
        docket.register(self.fn, names=[self.key])

    async def add_to_docket(
        self,
        docket: Docket,
        arguments: dict[str, Any],
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this tool for background execution via docket.

        FunctionTool splats the arguments dict since .fn expects **kwargs.

        Args:
            docket: The Docket instance
            arguments: Tool arguments
            fn_key: Function lookup key in Docket registry (defaults to self.key)
            task_key: Redis storage key for the result
            **kwargs: Additional kwargs passed to docket.add()
        """
        lookup_key = fn_key or self.key
        if task_key:
            kwargs["key"] = task_key
        return await docket.add(lookup_key, **kwargs)(**arguments)


@overload
def tool(fn: F) -> F: ...
@overload
def tool(
    name_or_fn: str,
    *,
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
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
) -> Callable[[F], F]: ...
@overload
def tool(
    name_or_fn: None = None,
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
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
) -> Callable[[F], F]: ...


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
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
) -> Any:
    """Standalone decorator to mark a function as an MCP tool.

    Returns the original function with metadata attached. Register with a server
    using mcp.add_tool().
    """
    if isinstance(annotations, dict):
        annotations = ToolAnnotations(**annotations)

    if isinstance(name_or_fn, classmethod):
        raise TypeError(
            "To decorate a classmethod, use @classmethod above @tool. "
            "See https://gofastmcp.com/servers/tools#using-with-methods"
        )

    def create_tool(fn: Callable[..., Any], tool_name: str | None) -> FunctionTool:
        # Create metadata first, then pass it
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
        )
        target = fn.__func__ if hasattr(fn, "__func__") else fn
        target.__fastmcp__ = metadata
        return fn

    def decorator(fn: F, tool_name: str | None) -> F:
        if fastmcp.settings.decorator_mode == "object":
            warnings.warn(
                "decorator_mode='object' is deprecated and will be removed in a future version. "
                "Decorators now return the original function with metadata attached.",
                DeprecationWarning,
                stacklevel=4,
            )
            return create_tool(fn, tool_name)  # type: ignore[return-value]
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
        raise TypeError(f"Invalid first argument: {type(name_or_fn)}")

    def wrapper(fn: F) -> F:
        return decorator(fn, tool_name)

    return wrapper
