from __future__ import annotations as _annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.shared.context import LifespanContextT
from mcp.types import EmbeddedResource, ImageContent, TextContent

from fastmcp.exceptions import NotFoundError
from fastmcp.settings import DuplicateBehavior
from fastmcp.tools.tool import Tool
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

    def has_tool(self, key: str) -> bool:
        """Check if a tool exists."""
        return key in self._tools

    def get_tool(self, key: str) -> Tool:
        """Get tool by key."""
        if key in self._tools:
            return self._tools[key]
        raise NotFoundError(f"Unknown tool: {key}")

    def get_tools(self) -> dict[str, Tool]:
        """Get all registered tools, indexed by registered key."""
        return self._tools

    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self.get_tools().values())

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
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        """Call a tool by name with arguments."""
        tool = self.get_tool(key)
        if not tool:
            raise NotFoundError(f"Unknown tool: {key}")

        return await tool.run(arguments, context=context)
