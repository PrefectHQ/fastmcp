"""A middleware for injecting tools into the MCP server context."""

from collections.abc import Sequence
from logging import Logger

import mcp_types
from typing_extensions import override

from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult
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
        context: MiddlewareContext[mcp_types.ListToolsRequest],
        call_next: CallNext[mcp_types.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Inject tools into the response."""
        return [*self._tools_to_inject, *await call_next(context)]

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[mcp_types.CallToolRequestParams],
        call_next: CallNext[mcp_types.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls to injected tools."""
        if context.message.name in self._tools_to_inject_by_name:
            tool = self._tools_to_inject_by_name[context.message.name]
            return await tool.run(arguments=context.message.arguments or {})

        return await call_next(context)
