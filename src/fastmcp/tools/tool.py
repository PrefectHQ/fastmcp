from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

import pydantic_core
from mcp.types import EmbeddedResource, ImageContent, TextContent
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, BeforeValidator, Field

from fastmcp.exceptions import ToolError
from fastmcp.utilities.func_metadata import FuncMetadata, func_metadata
from fastmcp.utilities.types import Image, _convert_set_defaults

if TYPE_CHECKING:
    from mcp.server.session import ServerSessionT
    from mcp.shared.context import LifespanContextT

    from fastmcp.server import Context


class Tool(BaseModel):
    """Internal tool registration info."""

    fn: Callable[..., Any]
    name: str = Field(description="Name of the tool")
    description: str = Field(description="Description of what the tool does")
    parameters: dict[str, Any] = Field(description="JSON schema for tool parameters")
    fn_metadata: FuncMetadata = Field(
        description="Metadata about the function including a pydantic model for tool"
        " arguments"
    )
    is_async: bool = Field(description="Whether the tool is async")
    context_kwarg: str | None = Field(
        None, description="Name of the kwarg that should receive context"
    )
    tags: Annotated[set[str], BeforeValidator(_convert_set_defaults)] = Field(
        default_factory=set, description="Tags for the tool"
    )

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        context_kwarg: str | None = None,
        tags: set[str] | None = None,
    ) -> Tool:
        """Create a Tool from a function."""
        from fastmcp import Context

        func_name = name or fn.__name__

        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        func_doc = description or fn.__doc__ or ""
        is_async = inspect.iscoroutinefunction(fn)

        if context_kwarg is None:
            if inspect.ismethod(fn) and hasattr(fn, "__func__"):
                sig = inspect.signature(fn.__func__)
            else:
                sig = inspect.signature(fn)
            for param_name, param in sig.parameters.items():
                if param.annotation is Context:
                    context_kwarg = param_name
                    break

        # Use callable typing to ensure fn is treated as a callable despite being a classmethod
        fn_callable: Callable[..., Any] = fn
        func_arg_metadata = func_metadata(
            fn_callable,
            skip_names=[context_kwarg] if context_kwarg is not None else [],
        )
        parameters = func_arg_metadata.arg_model.model_json_schema()

        return cls(
            fn=fn_callable,
            name=func_name,
            description=func_doc,
            parameters=parameters,
            fn_metadata=func_arg_metadata,
            is_async=is_async,
            context_kwarg=context_kwarg,
            tags=tags or set(),
        )

    async def run(
        self,
        arguments: dict[str, Any],
        context: Context[ServerSessionT, LifespanContextT] | None = None,
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        """Run the tool with arguments."""
        try:
            result = await self.fn_metadata.call_fn_with_arg_validation(
                self.fn,
                self.is_async,
                arguments,
                {self.context_kwarg: context}
                if self.context_kwarg is not None
                else None,
            )
            return _convert_to_content(result)
        except Exception as e:
            raise ToolError(f"Error executing tool {self.name}: {e}") from e

    def to_mcp_tool(self, **overrides: Any) -> MCPTool:
        kwargs = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }
        return MCPTool(**kwargs | overrides)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Tool):
            return False
        return self.model_dump() == other.model_dump()


def _convert_to_content(
    result: Any,
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
                other_content, _process_as_single_item=True
            )

        return other_content + mcp_types

    if not isinstance(result, str):
        try:
            result = json.dumps(pydantic_core.to_jsonable_python(result))
        except Exception:
            result = str(result)

    return [TextContent(type="text", text=result)]
