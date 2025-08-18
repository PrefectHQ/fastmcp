from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from fastmcp import settings
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.settings import DuplicateBehavior
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.tools.tool_transform import (
    ToolTransformConfig,
    apply_transformations_to_tools,
)
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import MountedServer

logger = get_logger(__name__)


class ToolManager:
    """Manages FastMCP tools."""

    def __init__(
        self,
        duplicate_behavior: DuplicateBehavior | None = None,
        mask_error_details: bool | None = None,
        transformations: dict[str, ToolTransformConfig] | None = None,
    ):
        self._tools: dict[str, Tool] = {}
        self._mounted_servers: list[MountedServer] = []
        self.mask_error_details = mask_error_details or settings.mask_error_details
        self.transformations = transformations or {}

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
        self._mounted_servers.append(server)

    async def _load_tools(self, *, via_server: bool = False) -> dict[str, Tool]:
        """
        The single, consolidated recursive method for fetching tools. The 'via_server'
        parameter determines the communication path.

        - via_server=False: Manager-to-manager path for complete, unfiltered inventory
        - via_server=True: Server-to-server path for filtered MCP requests
        """
        all_tools: dict[str, Tool] = {}

        for mounted in self._mounted_servers:
            try:
                if via_server:
                    # Use the server-to-server filtered path
                    child_results = await mounted.server._list_tools()
                else:
                    # Use the manager-to-manager unfiltered path
                    child_results = await mounted.server._tool_manager.list_tools()

                # The combination logic is the same for both paths
                child_dict = {t.key: t for t in child_results}
                if mounted.prefix:
                    for tool in child_dict.values():
                        prefixed_tool = tool.model_copy(
                            key=f"{mounted.prefix}_{tool.key}"
                        )
                        all_tools[prefixed_tool.key] = prefixed_tool
                else:
                    all_tools.update(child_dict)
            except Exception as e:
                # Skip failed mounts silently, matches existing behavior
                logger.warning(
                    f"Failed to get tools from server: {mounted.server.name!r}, mounted at: {mounted.prefix!r}: {e}"
                )
                if settings.mounted_components_raise_on_load_error:
                    raise
                continue

        # Finally, add local tools, which always take precedence
        all_tools.update(self._tools)

        transformed_tools = apply_transformations_to_tools(
            tools=all_tools,
            transformations=self.transformations,
        )

        return transformed_tools

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
        return await self._load_tools(via_server=False)

    async def list_tools(self) -> list[Tool]:
        """
        Lists all tools, applying protocol filtering.
        """
        tools_dict = await self._load_tools(via_server=True)
        return list(tools_dict.values())

    @property
    def _tools_transformed(self) -> list[str]:
        """Get the local tools."""

        return [
            transformation.name or tool_name
            for tool_name, transformation in self.transformations.items()
        ]

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

    def add_tool(self, tool: Tool) -> Tool:
        """Register a tool with the server."""
        existing = self._tools.get(tool.key)
        if existing:
            if self.duplicate_behavior == "warn":
                logger.warning(f"Tool already exists: {tool.key}")
                self._tools[tool.key] = tool
            elif self.duplicate_behavior == "replace":
                self._tools[tool.key] = tool
            elif self.duplicate_behavior == "error":
                raise ValueError(f"Tool already exists: {tool.key}")
            elif self.duplicate_behavior == "ignore":
                return existing
        else:
            self._tools[tool.key] = tool
        return tool

    def add_tool_transformation(
        self, tool_name: str, transformation: ToolTransformConfig
    ) -> None:
        """Add a tool transformation."""
        self.transformations[tool_name] = transformation

    def get_tool_transformation(self, tool_name: str) -> ToolTransformConfig | None:
        """Get a tool transformation."""
        return self.transformations.get(tool_name)

    def remove_tool_transformation(self, tool_name: str) -> None:
        """Remove a tool transformation."""
        if tool_name in self.transformations:
            del self.transformations[tool_name]

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

    async def call_tool(self, key: str, arguments: dict[str, Any]) -> ToolResult:
        """
        Internal API for servers: Finds and calls a tool, respecting the
        filtered protocol path.
        """
        # 1. Check local tools first. The server will have already applied its filter.
        if key in self._tools or key in self._tools_transformed:
            tool = await self.get_tool(key)
            if not tool:
                raise NotFoundError(f"Tool {key!r} not found")

            try:
                return await tool.run(arguments)

            # raise ToolErrors as-is
            except ToolError as e:
                logger.exception(f"Error calling tool {key!r}")
                raise e

            # Handle other exceptions
            except Exception as e:
                logger.exception(f"Error calling tool {key!r}")
                if self.mask_error_details:
                    # Mask internal details
                    raise ToolError(f"Error calling tool {key!r}") from e
                else:
                    # Include original error details
                    raise ToolError(f"Error calling tool {key!r}: {e}") from e

        # 2. Check mounted servers using the filtered protocol path.
        for mounted in reversed(self._mounted_servers):
            tool_key = key
            if mounted.prefix:
                if key.startswith(f"{mounted.prefix}_"):
                    tool_key = key.removeprefix(f"{mounted.prefix}_")
                else:
                    continue
            try:
                return await mounted.server._call_tool(tool_key, arguments)
            except NotFoundError:
                continue

        raise NotFoundError(f"Tool {key!r} not found.")
