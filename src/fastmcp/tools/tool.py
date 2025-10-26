from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Generic,
    Literal,
    TypeAlias,
    get_type_hints,
)

import mcp.types
import pydantic_core
from mcp.types import ContentBlock, Icon, TextContent, ToolAnnotations
from mcp.types import Tool as MCPTool
from pydantic import Field, PydanticSchemaGenerationError
from typing_extensions import TypeVar

import fastmcp
from fastmcp.server.dependencies import get_context
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    Audio,
    File,
    Image,
    NotSet,
    NotSetT,
    find_kwarg_by_type,
    get_cached_typeadapter,
    replace_type,
)

if TYPE_CHECKING:
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


class ToolResult:
    def __init__(
        self,
        content: list[ContentBlock] | Any | None = None,
        structured_content: dict[str, Any] | Any | None = None,
    ):
        if content is None and structured_content is None:
            raise ValueError("Either content or structured_content must be provided")
        elif content is None:
            content = structured_content

        self.content: list[ContentBlock] = _convert_to_content(result=content)

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
        self.structured_content: dict[str, Any] | None = structured_content

    def to_mcp_result(
        self,
    ) -> list[ContentBlock] | tuple[list[ContentBlock], dict[str, Any]]:
        if self.structured_content is None:
            return self.content
        return self.content, self.structured_content


class Tool(FastMCPComponent):
    """Internal tool registration info."""

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
    serializer: Annotated[
        ToolResultSerializerType | None,
        Field(description="Optional custom serializer for tool results"),
    ] = None

    def enable(self) -> None:
        super().enable()
        try:
            context = get_context()
            context._queue_tool_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

    def disable(self) -> None:
        super().disable()
        try:
            context = get_context()
            context._queue_tool_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

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
            _meta=overrides.get(
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
        output_schema: dict[str, Any] | Literal[False] | NotSetT | None = NotSet,
        serializer: ToolResultSerializerType | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool | None = None,
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
            enabled=enabled,
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
        output_schema: dict[str, Any] | Literal[False] | NotSetT | None = NotSet,
        serializer: ToolResultSerializerType | None = None,
        meta: dict[str, Any] | NotSetT | None = NotSet,
        transform_args: dict[str, ArgTransform] | None = None,
        enabled: bool | None = None,
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
            enabled=enabled,
        )


class FunctionTool(Tool):
    fn: Callable[..., Any]

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
        output_schema: dict[str, Any] | Literal[False] | NotSetT | None = NotSet,
        serializer: ToolResultSerializerType | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> FunctionTool:
        """Create a Tool from a function."""

        parsed_fn = ParsedFunction.from_function(fn, exclude_args=exclude_args)

        if name is None and parsed_fn.name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        if isinstance(output_schema, NotSetT):
            final_output_schema = parsed_fn.output_schema
        elif output_schema is False:
            # Handle False as deprecated synonym for None (deprecated in 2.11.4)
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "Passing output_schema=False is deprecated. Use output_schema=None instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            final_output_schema = None
        else:
            # At this point output_schema is not NotSetT and not False, so it must be dict | None
            final_output_schema = output_schema
        # Note: explicit schemas (dict) are used as-is without auto-wrapping

        # Validate that explicit schemas are object type for structured content
        if final_output_schema is not None and isinstance(final_output_schema, dict):
            if final_output_schema.get("type") != "object":
                raise ValueError(
                    f'Output schemas must have "type" set to "object" due to MCP spec limitations. Received: {final_output_schema!r}'
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
            enabled=enabled if enabled is not None else True,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Run the tool with arguments."""
        from fastmcp.server.context import Context

        arguments = arguments.copy()

        context_kwarg = find_kwarg_by_type(self.fn, kwarg_type=Context)
        if context_kwarg and context_kwarg not in arguments:
            arguments[context_kwarg] = get_context()

        type_adapter = get_cached_typeadapter(self.fn)
        result = type_adapter.validate_python(arguments)

        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, ToolResult):
            return result

        unstructured_result = _convert_to_content(result, serializer=self.serializer)

        if self.output_schema is None:
            # Do not produce a structured output for MCP Content Types
            if isinstance(result, ContentBlock | Audio | Image | File) or (
                isinstance(result, list | tuple)
                and any(isinstance(item, ContentBlock) for item in result)
            ):
                return ToolResult(content=unstructured_result)

            # Otherwise, try to serialize the result as a dict
            try:
                structured_content = pydantic_core.to_jsonable_python(result)
                if isinstance(structured_content, dict):
                    return ToolResult(
                        content=unstructured_result,
                        structured_content=structured_content,
                    )

            except pydantic_core.PydanticSerializationError:
                pass

            return ToolResult(content=unstructured_result)

        wrap_result = self.output_schema.get("x-fastmcp-wrap-result")

        return ToolResult(
            content=unstructured_result,
            structured_content={"result": result} if wrap_result else result,
        )


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
        from fastmcp.server.context import Context

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

        prune_params: list[str] = []
        context_kwarg = find_kwarg_by_type(fn, kwarg_type=Context)
        if context_kwarg:
            prune_params.append(context_kwarg)
        if exclude_args:
            prune_params.extend(exclude_args)

        input_type_adapter = get_cached_typeadapter(fn)
        input_schema = input_type_adapter.json_schema()
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
                if (
                    wrap_non_object_output_schema
                    and base_schema.get("type") != "object"
                ):
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
