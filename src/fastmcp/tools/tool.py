from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pydantic_core
from mcp.types import TextContent, ToolAnnotations
from mcp.types import Tool as MCPTool
from pydantic import Field

import fastmcp
from fastmcp.server.dependencies import get_context
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    Audio,
    File,
    Image,
    MCPContent,
    find_kwarg_by_type,
    get_cached_typeadapter,
)

if TYPE_CHECKING:
    from fastmcp.tools.tool_transform import ArgTransform, TransformedTool

logger = get_logger(__name__)


def default_serializer(data: Any) -> str:
    return pydantic_core.to_json(data, fallback=str, indent=2).decode()


class Tool(FastMCPComponent):
    """Internal tool registration info."""

    parameters: dict[str, Any] = Field(description="JSON schema for tool parameters")
    annotations: ToolAnnotations | None = Field(
        default=None, description="Additional annotations about the tool"
    )
    serializer: Callable[[Any], str] | None = Field(
        default=None, description="Optional custom serializer for tool results"
    )

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
            serializer=serializer,
            enabled=enabled,
        )

    async def run(self, arguments: dict[str, Any]) -> list[MCPContent]:
        """Run the tool with arguments."""
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
        serializer: Callable[[Any], str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionTool:
        """Create a Tool from a function."""

        parsed_fn = ParsedFunction.from_function(fn, exclude_args=exclude_args)

        if name is None and parsed_fn.name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        return cls(
            fn=parsed_fn.fn,
            name=name or parsed_fn.name,
            description=description or parsed_fn.description,
            parameters=parsed_fn.parameters,
            tags=tags or set(),
            annotations=annotations,
            serializer=serializer,
            enabled=enabled if enabled is not None else True,
        )

    async def run(self, arguments: dict[str, Any]) -> list[MCPContent]:
        """Run the tool with arguments."""
        from fastmcp.server.context import Context

        arguments = arguments.copy()

        context_kwarg = find_kwarg_by_type(self.fn, kwarg_type=Context)
        if context_kwarg and context_kwarg not in arguments:
            arguments[context_kwarg] = get_context()

        if fastmcp.settings.tool_attempt_parse_json_args:
            # Pre-parse data from JSON in order to handle cases like `["a", "b", "c"]`
            # being passed in as JSON inside a string rather than an actual list.
            #
            # Claude desktop is prone to this - in fact it seems incapable of NOT doing
            # this. For sub-models, it tends to pass dicts (JSON objects) as JSON strings,
            # which can be pre-parsed here.
            signature = inspect.signature(self.fn)
            for param_name in self.parameters["properties"]:
                arg = arguments.get(param_name, None)
                # if not in signature, we won't have annotations, so skip logic
                if param_name not in signature.parameters:
                    continue
                # if not a string, we won't have a JSON to parse, so skip logic
                if not isinstance(arg, str):
                    continue
                # skip if the type is a simple type (int, float, bool)
                if signature.parameters[param_name].annotation in (
                    int,
                    float,
                    bool,
                ):
                    continue
                try:
                    arguments[param_name] = json.loads(arg)

                except json.JSONDecodeError:
                    pass

        type_adapter = get_cached_typeadapter(self.fn)
        result = type_adapter.validate_python(arguments)
        if inspect.isawaitable(result):
            result = await result

        return _convert_to_content(result, serializer=self.serializer)


@dataclass
class ParsedFunction:
    fn: Callable[..., Any]
    name: str
    description: str | None
    parameters: dict[str, Any]

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

        type_adapter = get_cached_typeadapter(fn)
        schema = type_adapter.json_schema()

        prune_params: list[str] = []
        context_kwarg = find_kwarg_by_type(fn, kwarg_type=Context)
        if context_kwarg:
            prune_params.append(context_kwarg)
        if exclude_args:
            prune_params.extend(exclude_args)

        schema = compress_schema(schema, prune_params=prune_params)
        return cls(
            fn=fn,
            name=fn_name,
            description=fn_doc,
            parameters=schema,
        )


def _convert_to_content(
    result: Any,
    serializer: Callable[[Any], str] | None = None,
    _process_as_single_item: bool = False,
) -> list[MCPContent]:
    """Convert a result to a sequence of content objects."""
    if result is None:
        return []

    if isinstance(result, MCPContent):
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
            if isinstance(item, MCPContent | Image | Audio | File):
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
