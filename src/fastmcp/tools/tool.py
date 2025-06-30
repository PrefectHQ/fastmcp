from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Generic, Literal, TypeVar

import mcp.types
import pydantic_core
from mcp.types import ContentBlock, TextContent, ToolAnnotations
from mcp.types import Tool as MCPTool
from pydantic import Field, PydanticSchemaGenerationError

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

T = TypeVar("T")


@dataclass
class _WrappedResult(Generic[T]):
    """Generic wrapper for non-object return types."""

    result: T


class _UnserializableType:
    pass


def default_serializer(data: Any) -> str:
    return pydantic_core.to_json(data, fallback=str, indent=2).decode()


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

        self.content = _convert_to_content(content)

        if structured_content is not None:
            try:
                structured_content = pydantic_core.to_jsonable_python(
                    structured_content
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
        Callable[[Any], str] | None,
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

    def to_mcp_tool(self, **overrides: Any) -> MCPTool:
        if self.title:
            title = self.title
        elif self.annotations and self.annotations.title:
            title = self.annotations.title
        else:
            title = None

        kwargs = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
            "outputSchema": self.output_schema,
            "annotations": self.annotations,
            "title": title,
        }
        return MCPTool(**kwargs | overrides)

    @staticmethod
    def from_function(
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        exclude_args: list[str] | None = None,
        output_schema: dict[str, Any] | None | NotSetT | Literal[False] = NotSet,
        serializer: Callable[[Any], str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionTool:
        """Create a Tool from a function."""
        return FunctionTool.from_function(
            fn=fn,
            name=name,
            title=title,
            description=description,
            tags=tags,
            annotations=annotations,
            exclude_args=exclude_args,
            output_schema=output_schema,
            serializer=serializer,
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
        transform_fn: Callable[..., Any] | None = None,
        name: str | None = None,
        transform_args: dict[str, ArgTransform] | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        output_schema: dict[str, Any] | None | Literal[False] = None,
        serializer: Callable[[Any], str] | None = None,
        enabled: bool | None = None,
    ) -> TransformedTool:
        from fastmcp.tools.tool_transform import TransformedTool

        return TransformedTool.from_tool(
            tool=tool,
            transform_fn=transform_fn,
            name=name,
            transform_args=transform_args,
            description=description,
            tags=tags,
            annotations=annotations,
            output_schema=output_schema,
            serializer=serializer,
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
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        exclude_args: list[str] | None = None,
        output_schema: dict[str, Any] | None | NotSetT | Literal[False] = NotSet,
        serializer: Callable[[Any], str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionTool:
        """Create a Tool from a function."""

        parsed_fn = ParsedFunction.from_function(fn, exclude_args=exclude_args)

        if name is None and parsed_fn.name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        if isinstance(output_schema, NotSetT):
            output_schema = parsed_fn.output_schema
        elif output_schema is False:
            output_schema = None
        # Note: explicit schemas (dict) are used as-is without auto-wrapping

        # Validate that explicit schemas are object type for structured content
        if output_schema is not None and isinstance(output_schema, dict):
            if output_schema.get("type") != "object":
                raise ValueError(
                    f'Output schemas must have "type" set to "object" due to MCP spec limitations. Received: {output_schema!r}'
                )

        return cls(
            fn=parsed_fn.fn,
            name=name or parsed_fn.name,
            title=title,
            description=description or parsed_fn.description,
            parameters=parsed_fn.input_schema,
            output_schema=output_schema,
            annotations=annotations,
            tags=tags or set(),
            serializer=serializer,
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

        structured_output = None
        # First handle structured content based on output schema, if any
        if self.output_schema is not None:
            if self.output_schema.get("x-fastmcp-wrap-result"):
                # Schema says wrap - always wrap in result key
                structured_output = {"result": result}
            else:
                structured_output = result
        # If no output schema, try to serialize the result. If it is a dict, use
        # it as structured content. If it is not a dict, ignore it.
        if structured_output is None:
            try:
                structured_output = pydantic_core.to_jsonable_python(result)
                if not isinstance(structured_output, dict):
                    structured_output = None
            except Exception:
                pass

        return ToolResult(
            content=unstructured_result,
            structured_content=structured_output,
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
        input_schema = compress_schema(input_schema, prune_params=prune_params)

        output_schema = None
        output_type = inspect.signature(fn).return_annotation

        if output_type not in (inspect._empty, None, Any, ...):
            # there are a variety of types that we don't want to attempt to
            # serialize because they are either used by FastMCP internally,
            # or are MCP content types that explicitly don't form structured
            # content. By replacing them with an explicitly unserializable type,
            # we ensure that no output schema is automatically generated.
            clean_output_type = replace_type(
                output_type,
                {
                    t: _UnserializableType
                    for t in (
                        Image,
                        Audio,
                        File,
                        ToolResult,
                        mcp.types.TextContent,
                        mcp.types.ImageContent,
                        mcp.types.AudioContent,
                        mcp.types.ResourceLink,
                        mcp.types.EmbeddedResource,
                    )
                },
            )

            try:
                type_adapter = get_cached_typeadapter(clean_output_type)
                base_schema = type_adapter.json_schema()

                # Generate schema for wrapped type if it's non-object
                # because MCP requires that output schemas are objects
                if (
                    wrap_non_object_output_schema
                    and base_schema.get("type") != "object"
                ):
                    # Use the wrapped result schema directly
                    wrapped_type = _WrappedResult[clean_output_type]
                    wrapped_adapter = get_cached_typeadapter(wrapped_type)
                    output_schema = wrapped_adapter.json_schema()
                    output_schema["x-fastmcp-wrap-result"] = True
                else:
                    output_schema = base_schema

                output_schema = compress_schema(output_schema)

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


def _convert_to_content(
    result: Any,
    serializer: Callable[[Any], str] | None = None,
    _process_as_single_item: bool = False,
) -> list[ContentBlock]:
    """Convert a result to a sequence of content objects."""
    if result is None:
        return []

    if isinstance(result, ContentBlock):
        return [result]

    if isinstance(result, Image):
        return [result.to_image_content()]

    elif isinstance(result, Audio):
        return [result.to_audio_content()]

    elif isinstance(result, File):
        return [result.to_resource_content()]

    if isinstance(result, list | tuple) and not _process_as_single_item:
        # if the result is a list, then it could either be a list of MCP types,
        # or a "regular" list that the tool is returning, or a mix of both.
        #
        # so we extract all the MCP types / images and convert them as individual content elements,
        # and aggregate the rest as a single content element

        mcp_types = []
        other_content = []

        for item in result:
            if isinstance(item, ContentBlock | Image | Audio | File):
                mcp_types.append(_convert_to_content(item)[0])
            else:
                other_content.append(item)

        if other_content:
            other_content = _convert_to_content(
                other_content[0] if len(other_content) == 1 else other_content,
                serializer=serializer,
                _process_as_single_item=True,
            )

        return other_content + mcp_types

    if not isinstance(result, str):
        if serializer is None:
            result = default_serializer(result)
        else:
            try:
                result = serializer(result)
            except Exception as e:
                logger.warning(
                    "Error serializing tool result: %s",
                    e,
                    exc_info=True,
                )
                result = default_serializer(result)

    return [TextContent(type="text", text=result)]
