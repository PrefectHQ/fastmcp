"""A middleware for injecting tools into the MCP server context."""

from collections.abc import Sequence
from logging import Logger

from fastmcp.server.middleware.middleware import Middleware
from fastmcp.tools.base import Tool
from fastmcp.utilities.logging import get_logger

logger: Logger = get_logger(name=__name__)


class ToolInjectionMiddleware(Middleware):
    """A middleware for injecting tools into the context.

    The server this middleware is added to lists and calls the injected tools
    alongside its own. They are resolved by the server's tool lookup, so a
    tool's `auth` check and every middleware in the chain, including
    `AuthMiddleware`, apply to them as they do to registered tools, wherever
    this middleware sits in the chain. Server and session visibility settings
    do not apply to injected tools.

    Injected tools are matched by name alone; a requested version is ignored.
    An injected tool takes precedence over a registered tool with the same
    name, and the registered tool is no longer listed. When several injection
    middleware provide the same name, the one added first takes precedence.
    Injected tools follow their task configuration as registered tools do.
    """

    def __init__(self, tools: Sequence[Tool]):
        """Initialize the tool injection middleware."""
        self._tools_to_inject: Sequence[Tool] = tools
        self._tools_to_inject_by_name: dict[str, Tool] = {
            tool.name: tool for tool in tools
        }

    @property
    def injected_tools(self) -> Sequence[Tool]:
        """The tools this middleware injects, one per name.

        If several given tools share a name, the last one is kept, matching
        `get_injected_tool()`.
        """
        return list(self._tools_to_inject_by_name.values())

    def get_injected_tool(self, name: str) -> Tool | None:
        """Return the injected tool with this name, if there is one."""
        return self._tools_to_inject_by_name.get(name)
