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

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools.tool import Tool
from fastmcp.utilities.async_utils import gather
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.visibility import VisibilityFilter


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
        self._visibility = VisibilityFilter()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"

    def with_transforms(
        self,
        *,
        namespace: str | None = None,
        tool_renames: dict[str, str] | None = None,
    ) -> Provider:
        """Apply transformations to this provider's components.

        Returns a TransformingProvider that wraps this provider and applies
        the specified transformations. Can be chained - each call creates a
        new wrapper that composes with the previous.

        Args:
            namespace: Prefix for tools/prompts ("namespace_name"), path segment
                for resources ("protocol://namespace/path").
            tool_renames: Map of original_name → final_name. Tools in this map
                use the specified name instead of namespace prefixing.

        Returns:
            A TransformingProvider wrapping this provider.

        Example:
            ```python
            # Apply namespace to all components
            provider = MyProvider().with_transforms(namespace="db")
            # Tool "greet" becomes "db_greet"
            # Resource "resource://data" becomes "resource://db/data"

            # Rename specific tools (bypasses namespace for those tools)
            provider = MyProvider().with_transforms(
                namespace="api",
                tool_renames={"verbose_tool_name": "short"}
            )
            # "verbose_tool_name" → "short" (explicit rename)
            # "other_tool" → "api_other_tool" (namespace applied)

            # Stacking composes transformations
            provider = (
                MyProvider()
                .with_transforms(namespace="api")
                .with_transforms(tool_renames={"api_foo": "bar"})
            )
            # "foo" → "api_foo" (inner) → "bar" (outer)
            ```
        """
        from fastmcp.server.providers.transforming import TransformingProvider

        return TransformingProvider(
            self, namespace=namespace, tool_renames=tool_renames
        )

    def with_namespace(self, namespace: str) -> Provider:
        """Shorthand for with_transforms(namespace=...).

        Args:
            namespace: The namespace to apply.

        Returns:
            A TransformingProvider wrapping this provider.

        Example:
            ```python
            provider = MyProvider().with_namespace("db")
            # Equivalent to: MyProvider().with_transforms(namespace="db")
            ```
        """
        return self.with_transforms(namespace=namespace)

    async def list_tools(self) -> Sequence[Tool]:
        """Return all available tools.

        Override to provide tools dynamically.
        """
        return []

    async def get_tool(self, name: str) -> Tool | None:
        """Get a specific tool by name.

        Default implementation lists all tools and finds by name.
        Override for more efficient single-tool lookup.

        Returns:
            The Tool if found, or None to continue searching other providers.
        """
        tools = await self.list_tools()
        return next((t for t in tools if t.name == name), None)

    async def list_resources(self) -> Sequence[Resource]:
        """Return all available resources.

        Override to provide resources dynamically.
        """
        return []

    async def get_resource(self, uri: str) -> Resource | None:
        """Get a specific resource by URI.

        Default implementation lists all resources and finds by URI.
        Override for more efficient single-resource lookup.

        Returns:
            The Resource if found, or None to continue searching other providers.
        """
        resources = await self.list_resources()
        return next((r for r in resources if str(r.uri) == uri), None)

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """Return all available resource templates.

        Override to provide resource templates dynamically.
        """
        return []

    async def get_resource_template(self, uri: str) -> ResourceTemplate | None:
        """Get a resource template that matches the given URI.

        Default implementation lists all templates and finds one whose pattern
        matches the URI.
        Override for more efficient lookup.

        Returns:
            The ResourceTemplate if a matching one is found, or None to continue searching.
        """
        templates = await self.list_resource_templates()
        return next(
            (t for t in templates if t.matches(uri) is not None),
            None,
        )

    async def list_prompts(self) -> Sequence[Prompt]:
        """Return all available prompts.

        Override to provide prompts dynamically.
        """
        return []

    async def get_prompt(self, name: str) -> Prompt | None:
        """Get a specific prompt by name.

        Default implementation lists all prompts and finds by name.
        Override for more efficient single-prompt lookup.

        Returns:
            The Prompt if found, or None to continue searching other providers.
        """
        prompts = await self.list_prompts()
        return next((p for p in prompts if p.name == name), None)

    async def get_component(
        self, key: str
    ) -> Tool | Resource | ResourceTemplate | Prompt | None:
        """Get a component by its prefixed key.

        Args:
            key: The prefixed key (e.g., "tool:name", "resource:uri", "template:uri").

        Returns:
            The component if found, or None to continue searching other providers.
        """
        # Default implementation: fetch all component types in parallel
        # Exceptions propagate since return_exceptions=False
        results = await gather(
            self.list_tools(),
            self.list_resources(),
            self.list_resource_templates(),
            self.list_prompts(),
        )
        for components in results:
            for component in components:  # type: ignore[union-attr]
                if component.key == key:
                    return component
        return None

    # -------------------------------------------------------------------------
    # Task registration
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Return components that should be registered as background tasks.

        Override to customize which components are task-eligible.
        Default calls list_* methods and filters for components
        with task_config.mode != 'forbidden'.

        Used by the server during startup to register functions with Docket.
        """
        # Fetch all component types in parallel
        tools, resources, templates, prompts = await gather(
            self.list_tools(),
            self.list_resources(),
            self.list_resource_templates(),
            self.list_prompts(),
        )

        return [
            c
            for c in [*tools, *resources, *templates, *prompts]
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
            keys: Keys to enable (e.g., "tool:my_tool").
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
            keys: Keys to disable (e.g., "tool:my_tool").
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
