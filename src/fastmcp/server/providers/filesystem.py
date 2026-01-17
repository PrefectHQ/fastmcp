"""FileSystemProvider for filesystem-based component discovery.

FileSystemProvider scans a directory for Python files, imports them, and
registers any Tool, Resource, ResourceTemplate, or Prompt objects found.

Components are created using the standalone decorators from fastmcp.tools,
fastmcp.resources, and fastmcp.prompts:

Example:
    ```python
    # In mcp/tools.py
    from fastmcp.tools import tool

    @tool
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    # In main.py
    from pathlib import Path

    from fastmcp import FastMCP
    from fastmcp.server.providers import FileSystemProvider

    mcp = FastMCP("MyServer", providers=[FileSystemProvider(Path(__file__).parent / "mcp")])
    ```
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.filesystem_discovery import discover_and_import
from fastmcp.server.providers.local_provider import LocalProvider
from fastmcp.tools.tool import Tool
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.versions import VersionSpec

logger = get_logger(__name__)


class FileSystemProvider(LocalProvider):
    """Provider that discovers components from the filesystem.

    Scans a directory for Python files and registers any Tool, Resource,
    ResourceTemplate, or Prompt objects found. Components are created using
    the standalone decorators:
    - @tool from fastmcp.tools
    - @resource from fastmcp.resources
    - @prompt from fastmcp.prompts

    Args:
        root: Root directory to scan. Defaults to current directory.
        reload: If True, re-scan files on every request (dev mode).
            Defaults to False (scan once at init, cache results).

    Example:
        ```python
        # In mcp/tools.py
        from fastmcp.tools import tool

        @tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # In main.py
        from pathlib import Path

        from fastmcp import FastMCP
        from fastmcp.server.providers import FileSystemProvider

        # Path relative to this file
        mcp = FastMCP("MyServer", providers=[FileSystemProvider(Path(__file__).parent / "mcp")])

        # Dev mode - re-scan on every request
        mcp = FastMCP("MyServer", providers=[FileSystemProvider(Path(__file__).parent / "mcp", reload=True)])
        ```
    """

    def __init__(
        self,
        root: str | Path = ".",
        reload: bool = False,
    ) -> None:
        super().__init__(on_duplicate="replace")
        self._root = Path(root).resolve()
        self._reload = reload
        self._loaded = False
        # Track files we've warned about: path -> mtime when warned
        # Re-warn if file changes (mtime differs)
        self._warned_files: dict[Path, float] = {}
        # Lock for serializing reload operations (created lazily)
        self._reload_lock: asyncio.Lock | None = None

        # Always load once at init to catch errors early
        self._load_components()

    def _load_components(self) -> None:
        """Discover and register all components from the filesystem."""
        # Clear existing components if reloading
        if self._loaded:
            self._components.clear()

        result = discover_and_import(self._root)

        # Log warnings for failed files (only once per file version)
        for file_path, error in result.failed_files.items():
            try:
                current_mtime = file_path.stat().st_mtime
            except OSError:
                current_mtime = 0.0

            # Warn if we haven't warned about this file, or if it changed
            last_warned_mtime = self._warned_files.get(file_path)
            if last_warned_mtime is None or last_warned_mtime != current_mtime:
                logger.warning(f"Failed to import {file_path}: {error}")
                self._warned_files[file_path] = current_mtime

        # Clear warnings for files that now import successfully
        successful_files = {fp for fp, _ in result.components}
        for fp in successful_files:
            self._warned_files.pop(fp, None)

        for file_path, component in result.components:
            try:
                self._register_component(component)
            except Exception:
                logger.exception(
                    "Failed to register %s from %s",
                    getattr(component, "name", repr(component)),
                    file_path,
                )

        self._loaded = True
        logger.debug(
            f"FileSystemProvider loaded {len(self._components)} components from {self._root}"
        )

    def _register_component(self, component: FastMCPComponent) -> None:
        """Register a single component based on its type."""
        if isinstance(component, Tool):
            self.add_tool(component)
        elif isinstance(component, ResourceTemplate):
            self.add_template(component)
        elif isinstance(component, Resource):
            self.add_resource(component)
        elif isinstance(component, Prompt):
            self.add_prompt(component)
        else:
            logger.debug("Ignoring unknown component type: %r", type(component))

    async def _ensure_loaded(self) -> None:
        """Ensure components are loaded, reloading if in reload mode.

        Uses a lock to serialize concurrent reload operations and runs
        filesystem I/O off the event loop using asyncio.to_thread.
        """
        if not self._reload and self._loaded:
            return

        # Create lock lazily (can't create in __init__ without event loop)
        if self._reload_lock is None:
            self._reload_lock = asyncio.Lock()

        async with self._reload_lock:
            # Double-check after acquiring lock
            if self._reload or not self._loaded:
                await asyncio.to_thread(self._load_components)

    # Override provider methods to support reload mode

    async def _list_tools(self) -> Sequence[Tool]:
        """Return all tools, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super()._list_tools()

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super()._get_tool(name, version)

    async def _list_resources(self) -> Sequence[Resource]:
        """Return all resources, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super()._list_resources()

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get a resource by URI, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super()._get_resource(uri, version)

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """Return all resource templates, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super()._list_resource_templates()

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get a resource template, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super()._get_resource_template(uri, version)

    async def _list_prompts(self) -> Sequence[Prompt]:
        """Return all prompts, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super()._list_prompts()

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get a prompt by name, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super()._get_prompt(name, version)

    def __repr__(self) -> str:
        return f"FileSystemProvider(root={self._root!r}, reload={self._reload})"
