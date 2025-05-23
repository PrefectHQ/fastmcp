from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

import pydantic_core
from mcp.types import EmbeddedResource, ImageContent, TextContent, ToolAnnotations
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, BeforeValidator, Field

import fastmcp
from fastmcp.server.dependencies import get_context
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    Image,
    _convert_set_defaults,
    find_kwarg_by_type,
    get_cached_typeadapter,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def default_serializer(data: Any) -> str:
    return pydantic_core.to_json(data, fallback=str, indent=2).decode()


class Tool(BaseModel):
    """Internal tool registration info."""

    fn: Callable[..., Any]
    name: str = Field(description="Name of the tool")
    description: str = Field(description="Description of what the tool does")
    parameters: dict[str, Any] = Field(description="JSON schema for tool parameters")
    tags: Annotated[set[str], BeforeValidator(_convert_set_defaults)] = Field(
        default_factory=set, description="Tags for the tool"
    )
    annotations: ToolAnnotations | None = Field(
        None, description="Additional annotations about the tool"
    )
    serializer: Callable[[Any], str] | None = Field(
        None, description="Optional custom serializer for tool results"
    )

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        serializer: Callable[[Any], str] | None = None,
    ) -> Tool:
        """Create a Tool from a function."""
        from fastmcp.server.context import Context

        # Reject functions with *args or **kwargs
        sig = inspect.signature(fn)
        for param in sig.parameters.values():
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                raise ValueError("Functions with *args are not supported as tools")
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                raise ValueError("Functions with **kwargs are not supported as tools")

        func_name = name or getattr(fn, "__name__", None) or fn.__class__.__name__

        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        func_doc = description or fn.__doc__ or ""

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn):
            fn = fn.__call__

        type_adapter = get_cached_typeadapter(fn)
        schema = type_adapter.json_schema()

        context_kwarg = find_kwarg_by_type(fn, kwarg_type=Context)
        if context_kwarg:
            prune_params = [context_kwarg]
        else:
            prune_params = None

        schema = compress_schema(schema, prune_params=prune_params)

        return cls(
            fn=fn,
            name=func_name,
            description=func_doc,
            parameters=schema,
            tags=tags or set(),
            annotations=annotations,
            serializer=serializer,
        )

    async def run(
        self, arguments: dict[str, Any]
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        """Run the tool with arguments."""
        from fastmcp.server.context import Context

        arguments = arguments.copy()

        context_kwarg = find_kwarg_by_type(self.fn, kwarg_type=Context)
        if context_kwarg and context_kwarg not in arguments:
            arguments[context_kwarg] = get_context()

        if fastmcp.settings.settings.tool_attempt_parse_json_args:
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

    def to_mcp_tool(self, **overrides: Any) -> MCPTool:
        kwargs = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
            "annotations": self.annotations,
        }
        return MCPTool(**kwargs | overrides)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Tool):
            return False
        return self.model_dump() == other.model_dump()


def _convert_to_content(
    result: Any,
    serializer: Callable[[Any], str] | None = None,
    _process_as_single_item: bool = False,
) -> list[TextContent | ImageContent | EmbeddedResource]:
    """Convert a result to a sequence of content objects."""
    if result is None:
        return []

    if isinstance(result, TextContent | ImageContent | EmbeddedResource):
        return [result]

    if isinstance(result, Image):
        return [result.to_image_content()]

    if isinstance(result, list | tuple) and not _process_as_single_item:
        # if the result is a list, then it could either be a list of MCP types,
        # or a "regular" list that the tool is returning, or a mix of both.
        #
        # so we extract all the MCP types / images and convert them as individual content elements,
        # and aggregate the rest as a single content element

        mcp_types = []
        other_content = []

        for item in result:
            if isinstance(item, TextContent | ImageContent | EmbeddedResource | Image):
                mcp_types.append(_convert_to_content(item)[0])
            else:
                other_content.append(item)
        if other_content:
            other_content = _convert_to_content(
                other_content, serializer=serializer, _process_as_single_item=True
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
