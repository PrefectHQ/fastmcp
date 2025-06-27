from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

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
    StructuredOutput,
    find_kwarg_by_type,
    get_cached_typeadapter,
    replace_type,
)

if TYPE_CHECKING:
    from fastmcp.tools.tool_transform import ArgTransform, TransformedTool

logger = get_logger(__name__)


def default_serializer(data: Any) -> str:
    return pydantic_core.to_json(data, fallback=str, indent=2).decode()


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
        kwargs = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
            "outputSchema": self.output_schema,
            "annotations": self.annotations,
        }
        return MCPTool(**kwargs | overrides)

    @staticmethod
    def from_function(
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        exclude_args: list[str] | None = None,
        output_schema: dict[str, Any] | None | NotSetT = NotSet,
        serializer: Callable[[Any], str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionTool:
        """Create a Tool from a function."""
        return FunctionTool.from_function(
            fn=fn,
            name=name,
            description=description,
            tags=tags,
            annotations=annotations,
            exclude_args=exclude_args,
            output_schema=output_schema,
            serializer=serializer,
            enabled=enabled,
        )

    async def run(
        self, arguments: dict[str, Any]
    ) -> list[ContentBlock] | tuple[list[ContentBlock], dict[str, Any]]:
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
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        exclude_args: list[str] | None = None,
        output_schema: dict[str, Any] | None | NotSetT = NotSet,
        serializer: Callable[[Any], str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionTool:
        """Create a Tool from a function."""

        parsed_fn = ParsedFunction.from_function(fn, exclude_args=exclude_args)

        if name is None and parsed_fn.name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        if isinstance(output_schema, NotSetT):
            output_schema = parsed_fn.output_schema

        return cls(
            fn=parsed_fn.fn,
            name=name or parsed_fn.name,
            description=description or parsed_fn.description,
            parameters=parsed_fn.input_schema,
            output_schema=output_schema,
            annotations=annotations,
            tags=tags or set(),
            serializer=serializer,
            enabled=enabled if enabled is not None else True,
        )

    async def run(
        self, arguments: dict[str, Any]
    ) -> list[ContentBlock] | tuple[list[ContentBlock], dict[str, Any]]:
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

        unstructured_result = _convert_to_content(result, serializer=self.serializer)

        structured_result = None
        if isinstance(result, StructuredOutput):
            structured_result = result.to_structured_output()
        elif self.output_schema is not None:
            structured_result = pydantic_core.to_jsonable_python(result, fallback=str)

        # return only the unstructured result if there is no structured output
        if structured_result is None:
            return unstructured_result

        # return both the unstructured and structured results if there is structured output
        return (unstructured_result, structured_result)


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
        if output_type is not inspect._empty:
            try:
                replaced_output_type = replace_type(
                    output_type,
                    {
                        Image: mcp.types.ImageContent,
                        Audio: mcp.types.AudioContent,
                        File: mcp.types.EmbeddedResource,
                    },
                )
                output_type_adapter = get_cached_typeadapter(replaced_output_type)
                output_schema = output_type_adapter.json_schema()
            except PydanticSchemaGenerationError:
                logger.debug(f"Unable to generate schema for type {output_type!r}")

        return cls(
            fn=fn,
            name=fn_name,
            description=fn_doc,
            input_schema=input_schema,
            output_schema=output_schema,
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
