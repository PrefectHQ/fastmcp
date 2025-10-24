from __future__ import annotations

import warnings
from collections.abc import Callable, Mapping
from typing import Any

from mcp.types import ToolAnnotations
from pydantic import ValidationError

from fastmcp import settings
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.settings import DuplicateBehavior
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.tools.tool_transform import (
    ToolTransformConfig,
    apply_transformations_to_tools,
)
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class ToolManager:
    """Manages FastMCP tools."""

    def __init__(
        self,
        duplicate_behavior: DuplicateBehavior | None = None,
        mask_error_details: bool | None = None,
        transformations: Mapping[str, ToolTransformConfig] | None = None,
    ):
        self._tools: dict[str, Tool] = {}
        self.mask_error_details: bool = (
            mask_error_details or settings.mask_error_details
        )
        self.transformations: dict[str, ToolTransformConfig] = dict(
            transformations or {}
        )

        # Default to "warn" if None is provided
        if duplicate_behavior is None:
            duplicate_behavior = "warn"

        if duplicate_behavior not in DuplicateBehavior.__args__:
            raise ValueError(
                f"Invalid duplicate_behavior: {duplicate_behavior}. "
                f"Must be one of: {', '.join(DuplicateBehavior.__args__)}"
            )

        self.duplicate_behavior = duplicate_behavior

    async def _load_tools(self) -> dict[str, Tool]:
        """Return this manager's local tools with transformations applied."""
        transformed_tools = apply_transformations_to_tools(
            tools=self._tools,
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
        Gets the complete, unfiltered inventory of local tools.
        """
        return await self._load_tools()

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
        tool = await self.get_tool(key)
        try:
            return await tool.run(arguments)
        except ValidationError as e:
            logger.exception(f"Error validating tool {key!r}: {e}")
            raise e
        except ToolError as e:
            logger.exception(f"Error calling tool {key!r}")
            raise e
        except Exception as e:
            logger.exception(f"Error calling tool {key!r}")
            if self.mask_error_details:
                raise ToolError(f"Error calling tool {key!r}") from e
            else:
                raise ToolError(f"Error calling tool {key!r}: {e}") from e
