"""Providers for dynamic MCP components.

This module provides the `Provider` abstraction for providing tools,
resources, and prompts dynamically at runtime.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.providers import Provider
    from fastmcp.tools import Tool

    class DatabaseProvider(Provider):
        def __init__(self, db_url: str):
            self.db = Database(db_url)

        async def _list_tools(self) -> list[Tool]:
            rows = await self.db.fetch("SELECT * FROM tools")
            return [self._make_tool(row) for row in rows]

        async def _get_tool(self, name: str) -> Tool | None:
            row = await self.db.fetchone("SELECT * FROM tools WHERE name = ?", name)
            return self._make_tool(row) if row else None

    mcp = FastMCP("Server", providers=[DatabaseProvider(db_url)])
    ```
"""

from typing import TYPE_CHECKING

from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.fastmcp_provider import FastMCPProvider
from fastmcp.server.providers.filesystem import FileSystemProvider
from fastmcp.server.providers.local_provider import LocalProvider
from fastmcp.server.providers.skills import (
    ClaudeSkillsProvider,
    SkillProvider,
    SkillsDirectoryProvider,
    SkillsProvider,
)

if TYPE_CHECKING:
    from fastmcp.server.providers.openapi import OpenAPIProvider as OpenAPIProvider
    from fastmcp.server.providers.proxy import ProxyProvider as ProxyProvider

__all__ = [
    "AggregateProvider",
    "ClaudeSkillsProvider",
    "FastMCPProvider",
    "FileSystemProvider",
    "LocalProvider",
    "OpenAPIProvider",
    "Provider",
    "ProxyProvider",
    "SkillProvider",
    "SkillsDirectoryProvider",
    "SkillsProvider",  # Backwards compatibility alias for SkillsDirectoryProvider
]


def __getattr__(name: str):
    """Lazy import for providers to avoid circular imports."""
    if name == "ProxyProvider":
        from fastmcp.server.providers.proxy import ProxyProvider

        return ProxyProvider
    if name == "OpenAPIProvider":
        from fastmcp.server.providers.openapi import OpenAPIProvider

        return OpenAPIProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
