from __future__ import annotations as _annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.shared.context import LifespanContextT

from fastmcp.exceptions import ToolError
from fastmcp.settings import DuplicateBehavior
from fastmcp.tools.tool import Tool
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from mcp.server.session import ServerSessionT

    from fastmcp.server import Context

logger = get_logger(__name__)


class ToolManager:
    """Manages FastMCP tools."""

    def __init__(self, duplicate_behavior: DuplicateBehavior = DuplicateBehavior.WARN):
        self._tools: dict[str, Tool] = {}
        self.duplicate_behavior = duplicate_behavior

    def get_tool(self, name: str) -> Tool | None:
        """Get tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self._tools.values())

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

    def add_tool(self, tool: Tool) -> Tool:
        """Register a tool with the server."""
        existing = self._tools.get(tool.name)
        if existing:
            if self.duplicate_behavior == DuplicateBehavior.WARN:
                logger.warning(f"Tool already exists: {tool.name}")
                self._tools[tool.name] = tool
            elif self.duplicate_behavior == DuplicateBehavior.REPLACE:
                self._tools[tool.name] = tool
            elif self.duplicate_behavior == DuplicateBehavior.ERROR:
                raise ValueError(f"Tool already exists: {tool.name}")
            elif self.duplicate_behavior == DuplicateBehavior.IGNORE:
                pass
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

            new_tool = copy.copy(tool)
            new_tool.name = prefixed_name

            # Store the copied tool
            self.add_tool(new_tool)
            logger.debug(f'Imported tool "{name}" as "{prefixed_name}"')
