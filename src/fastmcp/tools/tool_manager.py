from __future__ import annotations as _annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.shared.context import LifespanContextT

from fastmcp.exceptions import ToolError
from fastmcp.tools.base import Tool
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from mcp.server.session import ServerSessionT

    from fastmcp.server import Context

logger = get_logger(__name__)


class ToolManager:
    """Manages FastMCP tools."""

    def __init__(self, warn_on_duplicate_tools: bool = True):
        self._tools: dict[str, Tool] = {}
        self.warn_on_duplicate_tools = warn_on_duplicate_tools

    def get_tool(self, name: str) -> Tool | None:
        """Get tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self._tools.values())

    def add_tool(
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
    ) -> Tool:
        """Add a tool to the server."""
        tool = Tool.from_function(fn, name=name, description=description)
        existing = self._tools.get(tool.name)
        if existing:
            if self.warn_on_duplicate_tools:
                logger.warning(f"Tool already exists: {tool.name}")
            return existing
        self._tools[tool.name] = tool
        return tool

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[ServerSessionT, LifespanContextT] | None = None,
    ) -> Any:
        """Call a tool by name with arguments."""
        tool = self.get_tool(name)
        if not tool:
            raise ToolError(f"Unknown tool: {name}")

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
            prefixed_name = f"{prefix}{name}" if prefix else name

            # Create a shallow copy of the tool with the prefixed name
            copied_tool = Tool.from_function(
                tool.fn,
                name=prefixed_name,
                description=tool.description,
            )

            # Store the copied tool
            self._tools[prefixed_name] = copied_tool
            logger.debug(f"Imported tool: {name} as {prefixed_name}")
