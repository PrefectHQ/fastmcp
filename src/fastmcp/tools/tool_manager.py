from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from mcp.types import ToolAnnotations

from fastmcp import settings
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.settings import DuplicateBehavior
from fastmcp.tools.tool import Tool
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import MCPContent

if TYPE_CHECKING:
    from fastmcp.server.server import MountedServer

logger = get_logger(__name__)


class ToolManager:
    """Manages FastMCP tools."""

    def __init__(
        self,
        duplicate_behavior: DuplicateBehavior | None = None,
        mask_error_details: bool | None = None,
    ):
        self._tools: dict[str, Tool] = {}
        self._mounted_sources: list[MountedServer] = []
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

    def mount(self, server: MountedServer) -> None:
        """Adds a mounted server as a source for tools."""
        self._mounted_sources.append(server)

    async def _load_tools(
        self, *, mode: Literal["inventory", "protocol"]
    ) -> dict[str, Tool]:
        """
        The single, consolidated recursive method for fetching tools. The 'mode'
        parameter determines the communication path.

        - mode="inventory": Manager-to-manager path for complete, unfiltered inventory
        - mode="protocol": Server-to-server path for filtered MCP requests
        """
        all_tools: dict[str, Tool] = {}

        for mounted in self._mounted_sources:
            try:
                if mode == "protocol":
                    # PATH 2: Use the server-to-server filtered path
                    child_results = await mounted.server._list_tools()
                else:  # mode == "inventory"
                    # PATH 1: Use the manager-to-manager unfiltered path
                    child_results = await mounted.server._tool_manager._list_tools()

                # The combination logic is the same for both paths
                child_dict = {t.key: t for t in child_results}
                if mounted.prefix:
                    for tool in child_dict.values():
                        prefixed_tool = tool.with_key(f"{mounted.prefix}_{tool.key}")
                        all_tools[prefixed_tool.key] = prefixed_tool
                else:
                    all_tools.update(child_dict)
            except Exception as e:
                # Skip failed mounts silently, matches existing behavior
                logger.warning(
                    f"Failed to get tools from mounted server '{mounted.prefix}': {e}"
                )
                continue

        # Finally, add local tools, which always take precedence
        all_tools.update(self._tools)
        return all_tools

    async def has_tool(self, key: str) -> bool:
        """Check if a tool exists."""
        tools = await self.get_tools()
        return key in tools

    async def get_tool(self, key: str) -> Tool:
        """Get tool by key."""
        tools = await self.get_tools()
        if key in tools:
            return tools[key]
        raise NotFoundError(f"Tool {key!r} not found")

    async def get_tools(self) -> dict[str, Tool]:
        """
        Gets the complete, unfiltered inventory of all tools.
        """
        return await self._load_tools(mode="inventory")

    async def _list_tools(self) -> list[Tool]:
        """
        Lists all tools, applying protocol filtering.
        """
        tools_dict = await self._load_tools(mode="protocol")
        return list(tools_dict.values())

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
        if settings.deprecation_warnings:
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
            raise NotFoundError(f"Tool {key!r} not found")

    async def call_tool(self, key: str, arguments: dict[str, Any]) -> list[MCPContent]:
        """
        Internal API for servers: Finds and calls a tool, respecting the
        filtered protocol path.
        """
        # 1. Check local tools first. The server will have already applied its filter.
        if key in self._tools:
            tool = await self.get_tool(key)
            if not tool:
                raise NotFoundError(f"Tool {key!r} not found")

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

        # 2. Check mounted servers using the filtered protocol path.
        for mounted in reversed(self._mounted_sources):
            if mounted.prefix and key.startswith(f"{mounted.prefix}_"):
                key_on_child = key.removeprefix(f"{mounted.prefix}_")
                try:
                    return await mounted.server._call_tool(key_on_child, arguments)
                except NotFoundError:
                    continue

        raise NotFoundError(f"Tool {key!r} not found.")
