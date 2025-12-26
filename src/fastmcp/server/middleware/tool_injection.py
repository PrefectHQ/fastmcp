"""A middleware for injecting tools into the MCP server context."""

from collections.abc import Sequence
from logging import Logger
from typing import Annotated, Any

import mcp.types
from mcp.types import Prompt
from pydantic import AnyUrl
from typing_extensions import override

from fastmcp.resources.resource import ResourceResult
from fastmcp.server.context import Context
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.logging import get_logger

logger: Logger = get_logger(name=__name__)


class ToolInjectionMiddleware(Middleware):
    """A middleware for injecting tools into the context."""

    def __init__(self, tools: Sequence[Tool]):
        """Initialize the tool injection middleware."""
        self._tools_to_inject: Sequence[Tool] = tools
        self._tools_to_inject_by_name: dict[str, Tool] = {
            tool.name: tool for tool in tools
        }

    @override
    async def on_list_tools(
        self,
        context: MiddlewareContext[mcp.types.ListToolsRequest],
        call_next: CallNext[mcp.types.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Inject tools into the response."""
        return [*self._tools_to_inject, *await call_next(context)]

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[mcp.types.CallToolRequestParams],
        call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls to injected tools."""
        if context.message.name in self._tools_to_inject_by_name:
            tool = self._tools_to_inject_by_name[context.message.name]
            return await tool.run(arguments=context.message.arguments or {})

        return await call_next(context)


async def list_prompts(context: Context) -> list[Prompt]:
    """List prompts available on the server."""
    return await context.list_prompts()


list_prompts_tool = Tool.from_function(
    fn=list_prompts,
)


async def get_prompt(
    context: Context,
    name: Annotated[str, "The name of the prompt to render."],
    arguments: Annotated[
        dict[str, Any] | None, "The arguments to pass to the prompt."
    ] = None,
) -> mcp.types.GetPromptResult:
    """Render a prompt available on the server."""
    return await context.get_prompt(name=name, arguments=arguments)


get_prompt_tool = Tool.from_function(
    fn=get_prompt,
)


class PromptToolMiddleware(ToolInjectionMiddleware):
    """A middleware for injecting prompts as tools into the context."""

    def __init__(self) -> None:
        tools: list[Tool] = [list_prompts_tool, get_prompt_tool]
        super().__init__(tools=tools)


async def list_resources(context: Context) -> list[mcp.types.Resource]:
    """List resources available on the server."""
    return await context.list_resources()


list_resources_tool = Tool.from_function(
    fn=list_resources,
)


async def read_resource(
    context: Context,
    uri: Annotated[AnyUrl | str, "The URI of the resource to read."],
) -> ResourceResult:
    """Read a resource available on the server."""
    return await context.read_resource(uri=uri)


read_resource_tool = Tool.from_function(
    fn=read_resource,
)


class ResourceToolMiddleware(ToolInjectionMiddleware):
    """A middleware for injecting resources as tools into the context."""

    def __init__(self) -> None:
        tools: list[Tool] = [list_resources_tool, read_resource_tool]
        super().__init__(tools=tools)
