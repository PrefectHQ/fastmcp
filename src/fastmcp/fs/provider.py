"""FileSystemProvider for filesystem-based component discovery.

FileSystemProvider scans a directory for Python files, imports them, and
registers any functions decorated with @tool, @resource, or @prompt.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.fs import FileSystemProvider

    mcp = FastMCP("MyServer", providers=[FileSystemProvider("mcp/")])
    ```
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastmcp.fs.decorators import PromptMeta, ResourceMeta, ToolMeta
from fastmcp.fs.discovery import discover_and_import
from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.local_provider import LocalProvider
from fastmcp.tools.tool import Tool
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class FileSystemProvider(LocalProvider):
    """Provider that discovers components from the filesystem.

    Scans a directory for Python files and registers functions decorated
    with @tool, @resource, or @prompt from fastmcp.fs.

    Args:
        root: Root directory to scan. Defaults to current directory.
        reload: If True, re-scan files on every request (dev mode).
            Defaults to False (scan once at init, cache results).

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.fs import FileSystemProvider

        # Basic usage
        mcp = FastMCP("MyServer", providers=[FileSystemProvider("mcp/")])

        # Dev mode - re-scan on every request
        mcp = FastMCP("MyServer", providers=[FileSystemProvider("mcp/", reload=True)])
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
            self._tool_transformations.clear()

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
        successful_files = {fp for fp, _, _ in result.components}
        for fp in successful_files:
            self._warned_files.pop(fp, None)

        for file_path, func, meta in result.components:
            try:
                self._register_component(func, meta)
            except Exception as e:
                logger.warning(
                    f"Failed to register {func.__name__} from {file_path}: {e}"
                )

        self._loaded = True
        logger.debug(
            f"FileSystemProvider loaded {len(self._components)} components from {self._root}"
        )

    def _register_component(
        self, func: Any, meta: ToolMeta | ResourceMeta | PromptMeta
    ) -> None:
        """Register a single component based on its metadata type."""
        if isinstance(meta, ToolMeta):
            self._register_tool(func, meta)
        elif isinstance(meta, ResourceMeta):
            self._register_resource(func, meta)
        elif isinstance(meta, PromptMeta):
            self._register_prompt(func, meta)

    def _register_tool(self, func: Any, meta: ToolMeta) -> None:
        """Register a tool from a decorated function."""
        tool = Tool.from_function(
            fn=func,
            name=meta.name,
            title=meta.title,
            description=meta.description,
            icons=meta.icons,
            tags=meta.tags,
            output_schema=meta.output_schema,
            annotations=meta.annotations,
            meta=meta.meta,
        )
        self.add_tool(tool)

    def _register_resource(self, func: Any, meta: ResourceMeta) -> None:
        """Register a resource or resource template from a decorated function."""
        uri = meta.uri

        # Check if this should be a template
        has_uri_params = "{" in uri and "}" in uri

        # Check for function parameters (excluding injected ones)
        from fastmcp.server.dependencies import without_injected_parameters

        wrapper_fn = without_injected_parameters(func)
        has_func_params = bool(inspect.signature(wrapper_fn).parameters)

        if has_uri_params or has_func_params:
            # Register as template
            template = ResourceTemplate.from_function(
                fn=func,
                uri_template=uri,
                name=meta.name,
                title=meta.title,
                description=meta.description,
                icons=meta.icons,
                mime_type=meta.mime_type,
                tags=meta.tags,
                annotations=meta.annotations,
                meta=meta.meta,
            )
            self.add_template(template)
        else:
            # Register as static resource
            resource = Resource.from_function(
                fn=func,
                uri=uri,
                name=meta.name,
                title=meta.title,
                description=meta.description,
                icons=meta.icons,
                mime_type=meta.mime_type,
                tags=meta.tags,
                annotations=meta.annotations,
                meta=meta.meta,
            )
            self.add_resource(resource)

    def _register_prompt(self, func: Any, meta: PromptMeta) -> None:
        """Register a prompt from a decorated function."""
        prompt = Prompt.from_function(
            fn=func,
            name=meta.name,
            title=meta.title,
            description=meta.description,
            icons=meta.icons,
            tags=meta.tags,
            meta=meta.meta,
        )
        self.add_prompt(prompt)

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

    async def list_tools(self) -> Sequence[Tool]:
        """Return all tools, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super().list_tools()

    async def get_tool(self, name: str) -> Tool | None:
        """Get a tool by name, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super().get_tool(name)

    async def list_resources(self) -> Sequence[Resource]:
        """Return all resources, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super().list_resources()

    async def get_resource(self, uri: str) -> Resource | None:
        """Get a resource by URI, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super().get_resource(uri)

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """Return all resource templates, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super().list_resource_templates()

    async def get_resource_template(self, uri: str) -> ResourceTemplate | None:
        """Get a resource template, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super().get_resource_template(uri)

    async def list_prompts(self) -> Sequence[Prompt]:
        """Return all prompts, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super().list_prompts()

    async def get_prompt(self, name: str) -> Prompt | None:
        """Get a prompt by name, reloading if in reload mode."""
        await self._ensure_loaded()
        return await super().get_prompt(name)

    def __repr__(self) -> str:
        return f"FileSystemProvider(root={self._root!r}, reload={self._reload})"
