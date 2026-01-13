"""Standalone @tool decorator for FastMCP."""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    Protocol,
    TypeVar,
    get_type_hints,
    overload,
    runtime_checkable,
)

import mcp.types
from mcp.types import Icon, ToolAnnotations, ToolExecution
from pydantic import PydanticSchemaGenerationError
from typing_extensions import TypeVar as TypeVarExt

import fastmcp
from fastmcp.decorators import resolve_task_config
from fastmcp.server.dependencies import (
    transform_context_annotations,
    without_injected_parameters,
)
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.tool import (
    AuthCheckCallable,
    Tool,
    ToolResult,
    ToolResultSerializerType,
)
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    Audio,
    File,
    Image,
    NotSet,
    NotSetT,
    create_function_without_params,
    get_cached_typeadapter,
    replace_type,
)

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

F = TypeVar("F", bound=Callable[..., Any])
T = TypeVarExt("T", default=Any)

logger = get_logger(__name__)


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
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None


@dataclass
class _WrappedResult(Generic[T]):
    """Generic wrapper for non-object return types."""

    result: T


class _UnserializableType:
    pass


def _is_object_schema(schema: dict[str, Any]) -> bool:
    """Check if a JSON schema represents an object type."""
    # Direct object type
    if schema.get("type") == "object":
        return True

    # Schema with properties but no explicit type is treated as object
    if "properties" in schema:
        return True

    # Self-referencing types use $ref pointing to $defs
    # The referenced type is always an object in our use case
    return "$ref" in schema and "$defs" in schema


@dataclass
class ParsedFunction:
    fn: Callable[..., Any]
    name: str
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        exclude_args: list[str] | None = None,
        validate: bool = True,
        wrap_non_object_output_schema: bool = True,
    ) -> ParsedFunction:
        if validate:
            sig = inspect.signature(fn)
            # Reject functions with *args or **kwargs
            for param in sig.parameters.values():
                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    raise ValueError("Functions with *args are not supported as tools")
                if param.kind == inspect.Parameter.VAR_KEYWORD:
                    raise ValueError(
                        "Functions with **kwargs are not supported as tools"
                    )

            # Reject exclude_args that don't exist in the function or don't have a default value
            if exclude_args:
                for arg_name in exclude_args:
                    if arg_name not in sig.parameters:
                        raise ValueError(
                            f"Parameter '{arg_name}' in exclude_args does not exist in function."
                        )
                    param = sig.parameters[arg_name]
                    if param.default == inspect.Parameter.empty:
                        raise ValueError(
                            f"Parameter '{arg_name}' in exclude_args must have a default value."
                        )

        # collect name and doc before we potentially modify the function
        fn_name = getattr(fn, "__name__", None) or fn.__class__.__name__
        fn_doc = inspect.getdoc(fn)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        # Transform Context type annotations to Depends() for unified DI
        fn = transform_context_annotations(fn)

        # Handle injected parameters (Context, Docket dependencies)
        wrapper_fn = without_injected_parameters(fn)

        # Also handle exclude_args with non-serializable types (issue #2431)
        # This must happen before Pydantic tries to serialize the parameters
        if exclude_args:
            wrapper_fn = create_function_without_params(wrapper_fn, list(exclude_args))

        input_type_adapter = get_cached_typeadapter(wrapper_fn)
        input_schema = input_type_adapter.json_schema()

        # Compress and handle exclude_args
        prune_params = list(exclude_args) if exclude_args else None
        input_schema = compress_schema(
            input_schema, prune_params=prune_params, prune_titles=True
        )

        output_schema = None
        # Get the return annotation from the signature
        sig = inspect.signature(fn)
        output_type = sig.return_annotation

        # If the annotation is a string (from __future__ annotations), resolve it
        if isinstance(output_type, str):
            try:
                # Use get_type_hints to resolve the return type
                # include_extras=True preserves Annotated metadata
                type_hints = get_type_hints(fn, include_extras=True)
                output_type = type_hints.get("return", output_type)
            except Exception as e:
                # If resolution fails, keep the string annotation
                logger.debug("Failed to resolve type hint for return annotation: %s", e)

        if output_type not in (inspect._empty, None, Any, ...):
            # there are a variety of types that we don't want to attempt to
            # serialize because they are either used by FastMCP internally,
            # or are MCP content types that explicitly don't form structured
            # content. By replacing them with an explicitly unserializable type,
            # we ensure that no output schema is automatically generated.
            clean_output_type = replace_type(
                output_type,
                dict.fromkeys(  # type: ignore[arg-type]
                    (
                        Image,
                        Audio,
                        File,
                        ToolResult,
                        mcp.types.TextContent,
                        mcp.types.ImageContent,
                        mcp.types.AudioContent,
                        mcp.types.ResourceLink,
                        mcp.types.EmbeddedResource,
                    ),
                    _UnserializableType,
                ),
            )

            try:
                type_adapter = get_cached_typeadapter(clean_output_type)
                base_schema = type_adapter.json_schema(mode="serialization")

                # Generate schema for wrapped type if it's non-object
                # because MCP requires that output schemas are objects
                # Check if schema is an object type, resolving $ref references
                # (self-referencing types use $ref at root level)
                if wrap_non_object_output_schema and not _is_object_schema(base_schema):
                    # Use the wrapped result schema directly
                    wrapped_type = _WrappedResult[clean_output_type]
                    wrapped_adapter = get_cached_typeadapter(wrapped_type)
                    output_schema = wrapped_adapter.json_schema(mode="serialization")
                    output_schema["x-fastmcp-wrap-result"] = True
                else:
                    output_schema = base_schema

                output_schema = compress_schema(output_schema, prune_titles=True)

            except PydanticSchemaGenerationError as e:
                if "_UnserializableType" not in str(e):
                    logger.debug(f"Unable to generate schema for type {output_type!r}")

        return cls(
            fn=fn,
            name=fn_name,
            description=fn_doc,
            input_schema=input_schema,
            output_schema=output_schema or None,
        )


class FunctionTool(Tool):
    fn: Callable[..., Any]

    def to_mcp_tool(
        self,
        *,
        include_fastmcp_meta: bool | None = None,
        **overrides: Any,
    ) -> mcp.types.Tool:
        """Convert the FastMCP tool to an MCP tool.

        Extends the base implementation to add task execution mode if enabled.
        """
        # Get base MCP tool from parent
        mcp_tool = super().to_mcp_tool(
            include_fastmcp_meta=include_fastmcp_meta, **overrides
        )

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
                    title,
                    description,
                    icons,
                    tags,
                    annotations,
                    meta,
                    task,
                    serializer,
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
            auth=metadata.auth,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Run the tool with arguments."""
        wrapper_fn = without_injected_parameters(self.fn)
        type_adapter = get_cached_typeadapter(wrapper_fn)
        result = type_adapter.validate_python(arguments)
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

    async def add_to_docket(  # type: ignore[override]
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
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
) -> Callable[[F], F]: ...
@overload
def tool(
    name_or_fn: None = None,
    *,
    name: str | None = None,
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
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
) -> Callable[[F], F]: ...


def tool(
    name_or_fn: str | Callable[..., Any] | None = None,
    *,
    name: str | None = None,
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
            auth=auth,
        )
        return FunctionTool.from_function(fn, metadata=tool_meta)

    def attach_metadata(fn: F, tool_name: str | None) -> F:
        metadata = ToolMeta(
            name=tool_name,
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
            auth=auth,
        )
        target = fn.__func__ if hasattr(fn, "__func__") else fn
        target.__fastmcp__ = metadata  # type: ignore[attr-defined]
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
