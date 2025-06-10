from __future__ import annotations as _annotations

import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.types import EmbeddedResource, ImageContent, TextContent, ToolAnnotations

from fastmcp import settings
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.settings import DuplicateBehavior
from fastmcp.tools.tool import Tool
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class ToolManager:
    """Manages FastMCP tools."""

    def __init__(
        self,
        duplicate_behavior: DuplicateBehavior | None = None,
        mask_error_details: bool | None = None,
    ):
        self._tools: dict[str, Tool] = {}
        self.mask_error_details = mask_error_details or settings.mask_error_details

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
        annotations: ToolAnnotations | None = None,
        serializer: Callable[[Any], str] | None = None,
        exclude_args: list[str] | None = None,
    ) -> Tool:
        """Add a tool to the server."""
        # deprecated in 2.7.0
        warnings.warn(
            "ToolManager.add_tool_from_fn() is deprecated. Use Tool.from_function() and call add_tool() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        tool = Tool.from_function(
            fn,
            name=name,
            description=description,
            tags=tags,
            annotations=annotations,
            exclude_args=exclude_args,
            serializer=serializer,
        )
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

    def remove_tool(self, key: str) -> None:
        """Remove a tool from the server.

        Args:
            key: The key of the tool to remove

        Raises:
            NotFoundError: If the tool is not found
        """
        if key in self._tools:
            del self._tools[key]
        else:
            raise NotFoundError(f"Unknown tool: {key}")

    async def call_tool(
        self, key: str, arguments: dict[str, Any]
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        """Call a tool by name with arguments."""
        tool = self.get_tool(key)
        if not tool:
            raise NotFoundError(f"Unknown tool: {key}")

        try:
            return await tool.run(arguments)

        # raise ToolErrors as-is
        except ToolError as e:
            logger.exception(f"Error calling tool {key!r}: {e}")
            raise e

        # Handle other exceptions
        except Exception as e:
            logger.exception(f"Error calling tool {key!r}: {e}")
            if self.mask_error_details:
                # Mask internal details
                raise ToolError(f"Error calling tool {key!r}") from e
            else:
                # Include original error details
                raise ToolError(f"Error calling tool {key!r}: {e}") from e
