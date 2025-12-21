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
from dataclasses import dataclass

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools.tool import Tool


@dataclass
class Components:
    """Collection of MCP components."""

    tools: Sequence[Tool] = ()
    resources: Sequence[Resource] = ()
    templates: Sequence[ResourceTemplate] = ()
    prompts: Sequence[Prompt] = ()


@dataclass
class TaskComponents:
    """Collection of components eligible for background task execution.

    Used by get_tasks() to return components for Docket registration.
    Components must implement register_with_docket() and add_to_docket().
    """

    tools: Sequence[Tool] = ()
    resources: Sequence[Resource] = ()
    templates: Sequence[ResourceTemplate] = ()
    prompts: Sequence[Prompt] = ()


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

    # -------------------------------------------------------------------------
    # Task registration
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> TaskComponents:
        """Return components that should be registered as background tasks.

        Override to customize which components are task-eligible.
        Default calls list_* methods and filters for function-based components
        with task_config.mode != 'forbidden'.

        Used by the server during startup to register functions with Docket.
        """
        from fastmcp.prompts.prompt import FunctionPrompt
        from fastmcp.resources.resource import FunctionResource
        from fastmcp.resources.template import FunctionResourceTemplate
        from fastmcp.tools.tool import FunctionTool

        all_tools = await self.list_tools()
        all_resources = await self.list_resources()
        all_templates = await self.list_resource_templates()
        all_prompts = await self.list_prompts()

        return TaskComponents(
            tools=[
                t
                for t in all_tools
                if isinstance(t, FunctionTool) and t.task_config.supports_tasks()
            ],
            resources=[
                r
                for r in all_resources
                if isinstance(r, FunctionResource) and r.task_config.supports_tasks()
            ],
            templates=[
                t
                for t in all_templates
                if isinstance(t, FunctionResourceTemplate)
                and t.task_config.supports_tasks()
            ],
            prompts=[
                p
                for p in all_prompts
                if isinstance(p, FunctionPrompt) and p.task_config.supports_tasks()
            ],
        )

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
