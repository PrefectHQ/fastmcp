from __future__ import annotations as _annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.shared.context import LifespanContextT

from fastmcp.exceptions import ToolError
from fastmcp.settings import DuplicateBehavior
from fastmcp.tools.tool import MCPTool, Tool
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from mcp.server.session import ServerSessionT

    from fastmcp.server import Context

logger = get_logger(__name__)


class ToolManager:
    """Manages FastMCP tools."""

    def __init__(self, duplicate_behavior: DuplicateBehavior | None = None):
        self._tools: dict[str, Tool] = {}

        # Default to "warn" if None is provided
        if duplicate_behavior is None:
            duplicate_behavior = "warn"

        if duplicate_behavior not in DuplicateBehavior.__args__:
            raise ValueError(
                f"Invalid duplicate_behavior: {duplicate_behavior}. "
                f"Must be one of: {', '.join(DuplicateBehavior.__args__)}"
            )

        self.duplicate_behavior = duplicate_behavior

    def get_tool(self, key: str) -> Tool | None:
        """Get tool by key."""
        return self._tools.get(key)

    def get_tools(self) -> dict[str, Tool]:
        """Get all registered tools, indexed by registered key."""
        return self._tools

    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self.get_tools().values())

    def list_mcp_tools(self) -> list[MCPTool]:
        """List all registered tools in the format expected by the low-level MCP server."""
        return [tool.to_mcp_tool(name=key) for key, tool in self._tools.items()]

    def add_tool_from_fn(
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
    ) -> Tool:
        """Add a tool to the server."""
        tool = Tool.from_function(fn, name=name, description=description, tags=tags)
        return self.add_tool(tool)

    def add_tool(self, tool: Tool, key: str | None = None) -> Tool:
        """Register a tool with the server."""
        key = key or tool.name
        existing = self._tools.get(key)
        if existing:
            if self.duplicate_behavior == "warn":
                logger.warning(f"Tool already exists: {key}")
                self._tools[key] = tool
            elif self.duplicate_behavior == "replace":
                self._tools[key] = tool
            elif self.duplicate_behavior == "error":
                raise ValueError(f"Tool already exists: {key}")
            elif self.duplicate_behavior == "ignore":
                return existing
        else:
            self._tools[key] = tool
        return tool

    async def call_tool(
        self,
        key: str,
        arguments: dict[str, Any],
        context: Context[ServerSessionT, LifespanContextT] | None = None,
    ) -> Any:
        """Call a tool by name with arguments."""
        tool = self.get_tool(key)
        if not tool:
            raise ToolError(f"Unknown tool: {key}")

        return await tool.run(arguments, context=context)

    def import_tools(
        self, tool_manager: ToolManager, prefix: str | None = None
    ) -> None:
        """
        Import all tools from another ToolManager with prefixed names.

        Args:
            tool_manager: Another ToolManager instance to import tools from
            prefix: Prefix to add to tool names, including the delimiter.
                   The resulting tool name will be in the format "{prefix}{original_name}"
                   if prefix is provided, otherwise the original name is used.
                   For example, with prefix "weather/" and tool "forecast",
                   the imported tool would be available as "weather/forecast"
        """
        for name, tool in tool_manager._tools.items():
            key = f"{prefix}{name}" if prefix else name
            self.add_tool(tool, key=key)
            logger.debug(f'Imported tool "{tool.name}" as "{key}"')
