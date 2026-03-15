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

        async def _list_tools(self) -> list[Tool]:
            rows = await self.db.fetch("SELECT * FROM tools")
            return [self._make_tool(row) for row in rows]

        async def _get_tool(self, name: str) -> Tool | None:
            row = await self.db.fetchone("SELECT * FROM tools WHERE name = ?", name)
            return self._make_tool(row) if row else None

    mcp = FastMCP("Server", providers=[DatabaseProvider(db_url)])
    ```
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING, Literal, cast

from typing_extensions import Self

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
        self._transforms: list[Transform] = []

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"

    @property
    def transforms(self) -> list[Transform]:
        """All transforms applied to components from this provider."""
        return list(self._transforms)

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

    def wrap_transform(self, transform: Transform) -> Provider:
        """Return a new provider with this transform applied (immutable).

        Unlike add_transform() which mutates this provider, wrap_transform()
        returns a new provider that wraps this one. The original provider
        is unchanged.

        This is useful when you want to apply transforms without side effects,
        such as adding the same provider to multiple aggregators with different
        namespaces.

        Args:
            transform: The transform to apply.

        Returns:
            A new provider that wraps this one with the transform applied.

        Example:
            ```python
            from fastmcp.server.transforms import Namespace

            provider = MyProvider()
            namespaced = provider.wrap_transform(Namespace("api"))
            # provider is unchanged
            # namespaced returns tools as "api_toolname"
            ```
        """
        # Import here to avoid circular imports
        from fastmcp.server.providers.wrapped_provider import _WrappedProvider

        return _WrappedProvider(self, transform)

    # -------------------------------------------------------------------------
    # Internal transform chain building
    # -------------------------------------------------------------------------

    async def list_tools(self) -> Sequence[Tool]:
        """List tools with all transforms applied.

        Applies transforms sequentially: base → transforms (in order).
        Each transform receives the result from the previous transform.
        Components may be marked as disabled but are NOT filtered here -
        filtering happens at the server level to allow session transforms to override.

        Returns:
            Transformed sequence of tools (including disabled ones).
        """
        tools = await self._list_tools()
        for transform in self.transforms:
            tools = await transform.list_tools(tools)
        return tools

    async def get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get tool by transformed name with all transforms applied.

        Note: This method does NOT filter disabled components. The Server
        (FastMCP) performs enabled filtering after all transforms complete,
        allowing session-level transforms to override provider-level disables.

        Args:
            name: The transformed tool name to look up.
            version: Optional version filter. If None, returns highest version.

        Returns:
            The tool if found (may be marked disabled), None if not found.
        """

        async def base(n: str, version: VersionSpec | None = None) -> Tool | None:
            return await self._get_tool(n, version)

        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_tool, call_next=chain)

        return await chain(name, version=version)

    async def list_resources(self) -> Sequence[Resource]:
        """List resources with all transforms applied.

        Components may be marked as disabled but are NOT filtered here.
        """
        resources = await self._list_resources()
        for transform in self.transforms:
            resources = await transform.list_resources(resources)
        return resources

    async def get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get resource by transformed URI with all transforms applied.

        Note: This method does NOT filter disabled components. The Server
        (FastMCP) performs enabled filtering after all transforms complete.

        Args:
            uri: The transformed resource URI to look up.
            version: Optional version filter. If None, returns highest version.

        Returns:
            The resource if found (may be marked disabled), None if not found.
        """

        async def base(u: str, version: VersionSpec | None = None) -> Resource | None:
            return await self._get_resource(u, version)

        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_resource, call_next=chain)

        return await chain(uri, version=version)

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List resource templates with all transforms applied.

        Components may be marked as disabled but are NOT filtered here.
        """
        templates = await self._list_resource_templates()
        for transform in self.transforms:
            templates = await transform.list_resource_templates(templates)
        return templates

    async def get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get resource template by transformed URI with all transforms applied.

        Note: This method does NOT filter disabled components. The Server
        (FastMCP) performs enabled filtering after all transforms complete.

        Args:
            uri: The transformed template URI to look up.
            version: Optional version filter. If None, returns highest version.

        Returns:
            The template if found (may be marked disabled), None if not found.
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
        """List prompts with all transforms applied.

        Components may be marked as disabled but are NOT filtered here.
        """
        prompts = await self._list_prompts()
        for transform in self.transforms:
            prompts = await transform.list_prompts(prompts)
        return prompts

    async def get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get prompt by transformed name with all transforms applied.

        Note: This method does NOT filter disabled components. The Server
        (FastMCP) performs enabled filtering after all transforms complete.

        Args:
            name: The transformed prompt name to look up.
            version: Optional version filter. If None, returns highest version.

        Returns:
            The prompt if found (may be marked disabled), None if not found.
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

        # Apply provider's own transforms sequentially
        # For tasks, we need the fully-transformed names
        for transform in self.transforms:
            tools = await transform.list_tools(tools)
            resources = await transform.list_resources(resources)
            templates = await transform.list_resource_templates(templates)
            prompts = await transform.list_prompts(prompts)

        return [
            c
            for c in [
                *tools,
                *resources,
                *templates,
                *prompts,
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
        names: set[str] | None = None,
        keys: set[str] | None = None,
        version: VersionSpec | None = None,
        tags: set[str] | None = None,
        components: set[Literal["tool", "resource", "template", "prompt"]]
        | None = None,
        only: bool = False,
    ) -> Self:
        """Enable components matching all specified criteria.

        Adds a visibility transform that marks matching components as enabled.
        Later transforms override earlier ones, so enable after disable makes
        the component enabled.

        With only=True, switches to allowlist mode - first disables everything,
        then enables matching components.

        Args:
            names: Component names or URIs to enable.
            keys: Component keys to enable (e.g., {"tool:my_tool@v1"}).
            version: Component version spec to enable (e.g., VersionSpec(eq="v1") or
                VersionSpec(gte="v2")). Unversioned components will not match.
            tags: Enable components with these tags.
            components: Component types to include (e.g., {"tool", "prompt"}).
            only: If True, ONLY enable matching components (allowlist mode).

        Returns:
            Self for method chaining.
        """
        if only:
            # Allowlist: disable everything, then enable matching
            # The enable transform runs later on return path, so it overrides
            self._transforms.append(Visibility(False, match_all=True))
        self._transforms.append(
            Visibility(
                True,
                names=names,
                keys=keys,
                version=version,
                components=set(components) if components else None,
                tags=set(tags) if tags else None,
            )
        )

        return self

    def disable(
        self,
        *,
        names: set[str] | None = None,
        keys: set[str] | None = None,
        version: VersionSpec | None = None,
        tags: set[str] | None = None,
        components: set[Literal["tool", "resource", "template", "prompt"]]
        | None = None,
    ) -> Self:
        """Disable components matching all specified criteria.

        Adds a visibility transform that marks matching components as disabled.
        Components can be re-enabled by calling enable() with matching criteria
        (the later transform wins).

        Args:
            names: Component names or URIs to disable.
            keys: Component keys to disable (e.g., {"tool:my_tool@v1"}).
            version: Component version spec to disable (e.g., VersionSpec(eq="v1") or
                VersionSpec(gte="v2")). Unversioned components will not match.
            tags: Disable components with these tags.
            components: Component types to include (e.g., {"tool", "prompt"}).

        Returns:
            Self for method chaining.
        """
        self._transforms.append(
            Visibility(
                False,
                names=names,
                keys=keys,
                version=version,
                components=set(components) if components else None,
                tags=set(tags) if tags else None,
            )
        )
        return self
