"""Base Provider class for dynamic MCP components.

This module provides the `Provider` abstraction for providing tools,
resources, and prompts dynamically at runtime.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.providers import Provider
    from fastmcp.tools import Tool

    class DatabaseProvider(Provider):
        def __init__(self, db_url: str):
            super().__init__()
            self.db = Database(db_url)

        async def list_tools(self) -> list[Tool]:
            rows = await self.db.fetch("SELECT * FROM tools")
            return [self._make_tool(row) for row in rows]

        async def get_tool(self, name: str) -> Tool | None:
            row = await self.db.fetchone("SELECT * FROM tools WHERE name = ?", name)
            return self._make_tool(row) if row else None

    mcp = FastMCP("Server", providers=[DatabaseProvider(db_url)])
    ```
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING, cast

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.transforms.visibility import Visibility
from fastmcp.tools.tool import Tool
from fastmcp.utilities.async_utils import gather
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.versions import VersionSpec, version_sort_key

if TYPE_CHECKING:
    from fastmcp.server.transforms import Transform


class Provider:
    """Base class for dynamic component providers.

    Subclass and override whichever methods you need. Default implementations
    return empty lists / None, so you only need to implement what your provider
    supports.

    Provider semantics:
        - Return `None` from `get_*` methods to indicate "I don't have it" (search continues)
        - Static components (registered via decorators) always take precedence over providers
        - Providers are queried in registration order; first non-None wins
        - Components execute themselves via run()/read()/render() - providers just source them

    Error handling:
        - `list_*` methods: Errors are logged and the provider returns empty (graceful degradation).
          This allows other providers to still contribute their components.
    """

    def __init__(self) -> None:
        self._visibility = Visibility()
        self._transforms: list[Transform] = []

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"

    @property
    def transforms(self) -> list[Transform]:
        """All transforms including visibility (applied last/outermost)."""
        return [*self._transforms, self._visibility]

    def add_transform(self, transform: Transform) -> None:
        """Add a transform to this provider.

        Transforms modify components (tools, resources, prompts) as they flow
        through the provider. They're applied in order - first added is innermost.

        Args:
            transform: The transform to add.

        Example:
            ```python
            from fastmcp.server.transforms import Namespace

            provider = MyProvider()
            provider.add_transform(Namespace("api"))
            # Tools become "api_toolname"
            ```
        """
        self._transforms.append(transform)

    # -------------------------------------------------------------------------
    # Internal transform chain building
    # -------------------------------------------------------------------------

    async def list_tools(self) -> Sequence[Tool]:
        """List tools with all transforms applied.

        Builds a middleware chain: base → transforms (in order).
        Each transform wraps the previous via call_next.

        Returns:
            Transformed sequence of tools.
        """

        async def base() -> Sequence[Tool]:
            return await self._list_tools()

        chain = base
        for transform in self.transforms:
            chain = partial(transform.list_tools, call_next=chain)

        return await chain()

    async def get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get tool by transformed name with all transforms applied.

        Args:
            name: The transformed tool name to look up.
            version: Optional version filter. If None, returns highest version.

        Returns:
            The tool if found and enabled, None otherwise.
        """

        async def base(n: str, version: VersionSpec | None = None) -> Tool | None:
            return await self._get_tool(n, version)

        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_tool, call_next=chain)

        return await chain(name, version=version)

    async def list_resources(self) -> Sequence[Resource]:
        """List resources with all transforms applied."""

        async def base() -> Sequence[Resource]:
            return await self._list_resources()

        chain = base
        for transform in self.transforms:
            chain = partial(transform.list_resources, call_next=chain)

        return await chain()

    async def get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get resource by transformed URI with all transforms applied.

        Args:
            uri: The transformed resource URI to look up.
            version: Optional version filter. If None, returns highest version.
        """

        async def base(u: str, version: VersionSpec | None = None) -> Resource | None:
            return await self._get_resource(u, version)

        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_resource, call_next=chain)

        return await chain(uri, version=version)

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List resource templates with all transforms applied."""

        async def base() -> Sequence[ResourceTemplate]:
            return await self._list_resource_templates()

        chain = base
        for transform in self.transforms:
            chain = partial(transform.list_resource_templates, call_next=chain)

        return await chain()

    async def get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get resource template by transformed URI with all transforms applied.

        Args:
            uri: The transformed template URI to look up.
            version: Optional version filter. If None, returns highest version.
        """

        async def base(
            u: str, version: VersionSpec | None = None
        ) -> ResourceTemplate | None:
            return await self._get_resource_template(u, version)

        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_resource_template, call_next=chain)

        return await chain(uri, version=version)

    async def list_prompts(self) -> Sequence[Prompt]:
        """List prompts with all transforms applied."""

        async def base() -> Sequence[Prompt]:
            return await self._list_prompts()

        chain = base
        for transform in self.transforms:
            chain = partial(transform.list_prompts, call_next=chain)

        return await chain()

    async def get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get prompt by transformed name with all transforms applied.

        Args:
            name: The transformed prompt name to look up.
            version: Optional version filter. If None, returns highest version.
        """

        async def base(n: str, version: VersionSpec | None = None) -> Prompt | None:
            return await self._get_prompt(n, version)

        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_prompt, call_next=chain)

        return await chain(name, version=version)

    # -------------------------------------------------------------------------
    # Private list/get methods (override these to provide components)
    # -------------------------------------------------------------------------

    async def _list_tools(self) -> Sequence[Tool]:
        """Return all available tools.

        Override to provide tools dynamically. Returns ALL versions of all tools.
        The server handles deduplication to show one tool per name.
        """
        return []

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a specific tool by name.

        Default implementation filters _list_tools() and picks the highest version
        that matches the spec.

        Args:
            name: The tool name.
            version: Optional version filter. If None, returns highest version.
                     If specified, returns highest version matching the spec.

        Returns:
            The Tool if found, or None to continue searching other providers.
        """
        tools = await self._list_tools()
        matching = [t for t in tools if t.name == name]
        if version:
            matching = [t for t in matching if version.matches(t.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)  # type: ignore[type-var]

    async def _list_resources(self) -> Sequence[Resource]:
        """Return all available resources.

        Override to provide resources dynamically. Returns ALL versions of all resources.
        The server handles deduplication to show one resource per URI.
        """
        return []

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get a specific resource by URI.

        Default implementation filters _list_resources() and returns highest
        version matching the spec.

        Args:
            uri: The resource URI.
            version: Optional version filter. If None, returns highest version.

        Returns:
            The Resource if found, or None to continue searching other providers.
        """
        resources = await self._list_resources()
        matching = [r for r in resources if str(r.uri) == uri]
        if version:
            matching = [r for r in matching if version.matches(r.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)  # type: ignore[type-var]

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """Return all available resource templates.

        Override to provide resource templates dynamically. Returns ALL versions.
        The server handles deduplication.
        """
        return []

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get a resource template that matches the given URI.

        Default implementation lists all templates, finds those whose pattern
        matches the URI, and returns the highest version matching the spec.

        Args:
            uri: The URI to match against templates.
            version: Optional version filter. If None, returns highest version.

        Returns:
            The ResourceTemplate if a matching one is found, or None to continue searching.
        """
        templates = await self._list_resource_templates()
        matching = [t for t in templates if t.matches(uri) is not None]
        if version:
            matching = [t for t in matching if version.matches(t.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)  # type: ignore[type-var]

    async def _list_prompts(self) -> Sequence[Prompt]:
        """Return all available prompts.

        Override to provide prompts dynamically. Returns ALL versions of all prompts.
        The server handles deduplication to show one prompt per name.
        """
        return []

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get a specific prompt by name.

        Default implementation filters _list_prompts() and picks the highest version
        matching the spec.

        Args:
            name: The prompt name.
            version: Optional version filter. If None, returns highest version.

        Returns:
            The Prompt if found, or None to continue searching other providers.
        """
        prompts = await self._list_prompts()
        matching = [p for p in prompts if p.name == name]
        if version:
            matching = [p for p in matching if version.matches(p.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)  # type: ignore[type-var]

    # -------------------------------------------------------------------------
    # Task registration
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Return components that should be registered as background tasks.

        Override to customize which components are task-eligible.
        Default calls list_* methods, applies provider transforms, and filters
        for components with task_config.mode != 'forbidden'.

        Used by the server during startup to register functions with Docket.
        """
        # Fetch all component types in parallel
        results = await gather(
            self._list_tools(),
            self._list_resources(),
            self._list_resource_templates(),
            self._list_prompts(),
        )
        tools = cast(Sequence[Tool], results[0])
        resources = cast(Sequence[Resource], results[1])
        templates = cast(Sequence[ResourceTemplate], results[2])
        prompts = cast(Sequence[Prompt], results[3])

        # Apply provider's own transforms to components using the chain pattern
        # For tasks, we need the fully-transformed names, so use the list_ chain
        # Note: We build mini-chains for each component type

        async def tools_base() -> Sequence[Tool]:
            return tools

        async def resources_base() -> Sequence[Resource]:
            return resources

        async def templates_base() -> Sequence[ResourceTemplate]:
            return templates

        async def prompts_base() -> Sequence[Prompt]:
            return prompts

        # Apply transforms in order (visibility last/outermost)
        tools_chain = tools_base
        resources_chain = resources_base
        templates_chain = templates_base
        prompts_chain = prompts_base

        for transform in self.transforms:
            tools_chain = partial(transform.list_tools, call_next=tools_chain)
            resources_chain = partial(
                transform.list_resources, call_next=resources_chain
            )
            templates_chain = partial(
                transform.list_resource_templates, call_next=templates_chain
            )
            prompts_chain = partial(transform.list_prompts, call_next=prompts_chain)

        transformed_tools = await tools_chain()
        transformed_resources = await resources_chain()
        transformed_templates = await templates_chain()
        transformed_prompts = await prompts_chain()

        return [
            c
            for c in [
                *transformed_tools,
                *transformed_resources,
                *transformed_templates,
                *transformed_prompts,
            ]
            if c.task_config.supports_tasks()
        ]

    # -------------------------------------------------------------------------
    # Lifecycle methods
    # -------------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """User-overridable lifespan for custom setup and teardown.

        Override this method to perform provider-specific initialization
        like opening database connections, setting up external resources,
        or other state management needed for the provider's lifetime.

        The lifespan scope matches the server's lifespan - code before yield
        runs at startup, code after yield runs at shutdown.

        Example:
            ```python
            @asynccontextmanager
            async def lifespan(self):
                # Setup
                self.db = await connect_database()
                try:
                    yield
                finally:
                    # Teardown
                    await self.db.close()
            ```
        """
        yield

    # -------------------------------------------------------------------------
    # Enable/Disable
    # -------------------------------------------------------------------------

    def enable(
        self,
        *,
        keys: Sequence[str] | None = None,
        tags: set[str] | None = None,
        only: bool = False,
    ) -> None:
        """Enable components by removing from blocklist, or set allowlist with only=True.

        Args:
            keys: Keys to enable (e.g., "tool:my_tool@" for unversioned, "tool:my_tool@1.0" for versioned).
            tags: Tags to enable - components with these tags will be enabled.
            only: If True, switches to allowlist mode - ONLY show these keys/tags.
        """
        self._visibility.enable(keys=keys, tags=tags, only=only)

    def disable(
        self,
        *,
        keys: Sequence[str] | None = None,
        tags: set[str] | None = None,
    ) -> None:
        """Disable components by adding to the blocklist.

        Args:
            keys: Keys to disable (e.g., "tool:my_tool@" for unversioned, "tool:my_tool@1.0" for versioned).
            tags: Tags to disable - components with these tags will be disabled.
        """
        self._visibility.disable(keys=keys, tags=tags)

    def _is_component_enabled(self, component: FastMCPComponent) -> bool:
        """Check if a component is enabled.

        Delegates to the visibility filter which handles blocklist and allowlist logic.

        Args:
            component: The component to check.

        Returns:
            True if the component should be served, False otherwise.
        """
        return self._visibility.is_enabled(component)
