from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Generic,
    TypeAlias,
    get_type_hints,
    overload,
)

import mcp.types
import pydantic_core
from mcp.shared.tool_name_validation import validate_and_warn_tool_name
from mcp.types import (
    CallToolResult,
    ContentBlock,
    Icon,
    TextContent,
    ToolAnnotations,
    ToolExecution,
)
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, Field, PydanticSchemaGenerationError, model_validator
from typing_extensions import TypeVar

import fastmcp
from fastmcp.server.dependencies import without_injected_parameters
from fastmcp.server.tasks.config import TaskConfig, TaskMeta
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema, resolve_root_ref
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

    from fastmcp.tools.tool_transform import ArgTransform, TransformedTool

logger = get_logger(__name__)

T = TypeVar("T", default=Any)


@dataclass
class _WrappedResult(Generic[T]):
    """Generic wrapper for non-object return types."""

    result: T


class _UnserializableType:
    pass


ToolResultSerializerType: TypeAlias = Callable[[Any], str]


def default_serializer(data: Any) -> str:
    return pydantic_core.to_json(data, fallback=str).decode()


class ToolResult(BaseModel):
    content: list[ContentBlock] = Field(
        description="List of content blocks for the tool result"
    )
    structured_content: dict[str, Any] | None = Field(
        default=None, description="Structured content matching the tool's output schema"
    )
    meta: dict[str, Any] | None = Field(
        default=None, description="Runtime metadata about the tool execution"
    )

    def __init__(
        self,
        content: list[ContentBlock] | Any | None = None,
        structured_content: dict[str, Any] | Any | None = None,
        meta: dict[str, Any] | None = None,
    ):
        if content is None and structured_content is None:
            raise ValueError("Either content or structured_content must be provided")
        elif content is None:
            content = structured_content

        converted_content: list[ContentBlock] = _convert_to_content(result=content)

        if structured_content is not None:
            try:
                structured_content = pydantic_core.to_jsonable_python(
                    value=structured_content
                )
            except pydantic_core.PydanticSerializationError as e:
                logger.error(
                    f"Could not serialize structured content. If this is unexpected, set your tool's output_schema to None to disable automatic serialization: {e}"
                )
                raise
            if not isinstance(structured_content, dict):
                raise ValueError(
                    "structured_content must be a dict or None. "
                    f"Got {type(structured_content).__name__}: {structured_content!r}. "
                    "Tools should wrap non-dict values based on their output_schema."
                )

        super().__init__(
            content=converted_content, structured_content=structured_content, meta=meta
        )

    def to_mcp_result(
        self,
    ) -> (
        list[ContentBlock] | tuple[list[ContentBlock], dict[str, Any]] | CallToolResult
    ):
        if self.meta is not None:
            return CallToolResult(
                structuredContent=self.structured_content,
                content=self.content,
                _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
            )
        if self.structured_content is None:
            return self.content
        return self.content, self.structured_content


class Tool(FastMCPComponent):
    """Internal tool registration info."""

    KEY_PREFIX: ClassVar[str] = "tool"

    parameters: Annotated[
        dict[str, Any], Field(description="JSON schema for tool parameters")
    ]
    output_schema: Annotated[
        dict[str, Any] | None, Field(description="JSON schema for tool output")
    ] = None
    annotations: Annotated[
        ToolAnnotations | None,
        Field(description="Additional annotations about the tool"),
    ] = None
    execution: Annotated[
        ToolExecution | None,
        Field(description="Task execution configuration (SEP-1686)"),
    ] = None
    serializer: Annotated[
        ToolResultSerializerType | None,
        Field(
            description="Deprecated. Return ToolResult from your tools for full control over serialization."
        ),
    ] = None

    @model_validator(mode="after")
    def _validate_tool_name(self) -> Tool:
        """Validate tool name according to MCP specification (SEP-986)."""
        validate_and_warn_tool_name(self.name)
        return self

    def to_mcp_tool(
        self,
        *,
        include_fastmcp_meta: bool | None = None,
        **overrides: Any,
    ) -> MCPTool:
        """Convert the FastMCP tool to an MCP tool."""
        title = None

        if self.title:
            title = self.title
        elif self.annotations and self.annotations.title:
            title = self.annotations.title

        return MCPTool(
            name=overrides.get("name", self.name),
            title=overrides.get("title", title),
            description=overrides.get("description", self.description),
            inputSchema=overrides.get("inputSchema", self.parameters),
            outputSchema=overrides.get("outputSchema", self.output_schema),
            icons=overrides.get("icons", self.icons),
            annotations=overrides.get("annotations", self.annotations),
            execution=overrides.get("execution", self.execution),
            _meta=overrides.get(  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                "_meta", self.get_meta(include_fastmcp_meta=include_fastmcp_meta)
            ),
        )

    @staticmethod
    def from_function(
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        exclude_args: list[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        serializer: ToolResultSerializerType | None = None,  # Deprecated
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionTool:
        """Create a Tool from a function."""
        return FunctionTool.from_function(
            fn=fn,
            name=name,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            annotations=annotations,
            exclude_args=exclude_args,
            output_schema=output_schema,
            serializer=serializer,
            meta=meta,
            task=task,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """
        Run the tool with arguments.

        This method is not implemented in the base Tool class and must be
        implemented by subclasses.

        `run()` can EITHER return a list of ContentBlocks, or a tuple of
        (list of ContentBlocks, dict of structured output).
        """
        raise NotImplementedError("Subclasses must implement run()")

    def convert_result(self, raw_value: Any) -> ToolResult:
        """Convert a raw result to ToolResult.

        Handles ToolResult passthrough and converts raw values using the tool's
        attributes (serializer, output_schema) for proper conversion.
        """
        if isinstance(raw_value, ToolResult):
            return raw_value

        content = _convert_to_content(raw_value, serializer=self.serializer)

        # Skip structured content for ContentBlock types only if no output_schema
        # (if output_schema exists, MCP SDK requires structured_content)
        if self.output_schema is None and (
            isinstance(raw_value, ContentBlock | Audio | Image | File)
            or (
                isinstance(raw_value, list | tuple)
                and any(isinstance(item, ContentBlock) for item in raw_value)
            )
        ):
            return ToolResult(content=content)

        try:
            structured = pydantic_core.to_jsonable_python(raw_value)
        except pydantic_core.PydanticSerializationError:
            return ToolResult(content=content)

        if self.output_schema is None:
            # No schema - only use structured_content for dicts
            if isinstance(structured, dict):
                return ToolResult(content=content, structured_content=structured)
            return ToolResult(content=content)

        # Has output_schema - wrap if x-fastmcp-wrap-result is set
        wrap_result = self.output_schema.get("x-fastmcp-wrap-result")
        return ToolResult(
            content=content,
            structured_content={"result": structured} if wrap_result else structured,
        )

    @overload
    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: None = None,
    ) -> ToolResult: ...

    @overload
    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: TaskMeta | None = None,
    ) -> ToolResult | mcp.types.CreateTaskResult:
        """Server entry point that handles task routing.

        This allows ANY Tool subclass to support background execution by setting
        task_config.mode to "supported" or "required". The server calls this
        method instead of run() directly.

        Args:
            arguments: Tool arguments
            task_meta: If provided, execute as background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return ToolResult.

        Returns:
            ToolResult when task_meta is None.
            CreateTaskResult when task_meta is provided.

        Subclasses can override this to customize task routing behavior.
        For example, FastMCPProviderTool overrides to delegate to child
        middleware without submitting to Docket.
        """
        from fastmcp.server.tasks.routing import check_background_task

        task_result = await check_background_task(
            component=self,
            task_type="tool",
            arguments=arguments,
            task_meta=task_meta,
        )
        if task_result:
            return task_result

        return await self.run(arguments)

    def register_with_docket(self, docket: Docket) -> None:
        """Register this tool with docket for background execution."""
        if not self.task_config.supports_tasks():
            return
        docket.register(self.run, names=[self.key])

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
        return await docket.add(lookup_key, **kwargs)(arguments)

    @classmethod
    def from_tool(
        cls,
        tool: Tool,
        *,
        name: str | None = None,
        title: str | NotSetT | None = NotSet,
        description: str | NotSetT | None = NotSet,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | NotSetT | None = NotSet,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        serializer: ToolResultSerializerType | None = None,  # Deprecated
        meta: dict[str, Any] | NotSetT | None = NotSet,
        transform_args: dict[str, ArgTransform] | None = None,
        transform_fn: Callable[..., Any] | None = None,
    ) -> TransformedTool:
        from fastmcp.tools.tool_transform import TransformedTool

        return TransformedTool.from_tool(
            tool=tool,
            transform_fn=transform_fn,
            name=name,
            title=title,
            transform_args=transform_args,
            description=description,
            tags=tags,
            annotations=annotations,
            output_schema=output_schema,
            serializer=serializer,
            meta=meta,
        )


class FunctionTool(Tool):
    fn: Callable[..., Any]

    def to_mcp_tool(
        self,
        *,
        include_fastmcp_meta: bool | None = None,
        **overrides: Any,
    ) -> MCPTool:
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
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        exclude_args: list[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        serializer: ToolResultSerializerType | None = None,  # Deprecated
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionTool:
        """Create a Tool from a function."""
        if serializer is not None and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The `serializer` parameter is deprecated. "
                "Return ToolResult from your tools for full control over serialization. "
                "See https://gofastmcp.com/servers/tools#custom-serialization for migration examples.",
                DeprecationWarning,
                stacklevel=2,
            )
        if exclude_args and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The `exclude_args` parameter is deprecated as of FastMCP 2.14. "
                "Use dependency injection with `Depends()` instead for better lifecycle management. "
                "See https://gofastmcp.com/servers/dependencies for examples.",
                DeprecationWarning,
                stacklevel=2,
            )

        parsed_fn = ParsedFunction.from_function(fn, exclude_args=exclude_args)
        func_name = name or parsed_fn.name

        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        # Normalize task to TaskConfig and validate
        if task is None:
            task_config = TaskConfig(mode="forbidden")
        elif isinstance(task, bool):
            task_config = TaskConfig.from_bool(task)
        else:
            task_config = task
        task_config.validate_function(fn, func_name)

        if isinstance(output_schema, NotSetT):
            final_output_schema = parsed_fn.output_schema
        else:
            # At this point output_schema is not NotSetT, so it must be dict | None
            final_output_schema = output_schema
        # Note: explicit schemas (dict) are used as-is without auto-wrapping

        # Validate that explicit schemas are object type for structured content
        # (resolving $ref references for self-referencing types)
        if final_output_schema is not None and isinstance(final_output_schema, dict):
            if not _is_object_schema(final_output_schema):
                raise ValueError(
                    f"Output schemas must represent object types due to MCP spec limitations. Received: {final_output_schema!r}"
                )

        return cls(
            fn=parsed_fn.fn,
            name=name or parsed_fn.name,
            title=title,
            description=description or parsed_fn.description,
            icons=icons,
            parameters=parsed_fn.input_schema,
            output_schema=final_output_schema,
            annotations=annotations,
            tags=tags or set(),
            serializer=serializer,
            meta=meta,
            task_config=task_config,
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
            except Exception:
                # If resolution fails, keep the string annotation
                pass

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

                # Resolve root-level $ref to meet MCP spec requirement for type: object
                # Self-referential Pydantic models generate schemas with $ref at root
                output_schema = resolve_root_ref(output_schema)

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


def _serialize_with_fallback(
    result: Any, serializer: ToolResultSerializerType | None = None
) -> str:
    if serializer is not None:
        try:
            return serializer(result)
        except Exception as e:
            logger.warning(
                "Error serializing tool result: %s",
                e,
                exc_info=True,
            )

    return default_serializer(result)


def _convert_to_single_content_block(
    item: Any,
    serializer: ToolResultSerializerType | None = None,
) -> ContentBlock:
    if isinstance(item, ContentBlock):
        return item

    if isinstance(item, Image):
        return item.to_image_content()

    if isinstance(item, Audio):
        return item.to_audio_content()

    if isinstance(item, File):
        return item.to_resource_content()

    if isinstance(item, str):
        return TextContent(type="text", text=item)

    return TextContent(type="text", text=_serialize_with_fallback(item, serializer))


def _convert_to_content(
    result: Any,
    serializer: ToolResultSerializerType | None = None,
) -> list[ContentBlock]:
    """Convert a result to a sequence of content objects."""

    if result is None:
        return []

    if not isinstance(result, (list | tuple)):
        return [_convert_to_single_content_block(result, serializer)]

    # If all items are ContentBlocks, return them as is
    if all(isinstance(item, ContentBlock) for item in result):
        return result

    # If any item is a ContentBlock, convert non-ContentBlock items to TextContent
    # without aggregating them
    if any(isinstance(item, ContentBlock | Image | Audio | File) for item in result):
        return [
            _convert_to_single_content_block(item, serializer)
            if not isinstance(item, ContentBlock)
            else item
            for item in result
        ]
    # If none of the items are ContentBlocks, aggregate all items into a single TextContent
    return [TextContent(type="text", text=_serialize_with_fallback(result, serializer))]
