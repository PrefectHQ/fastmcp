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
from typing import TYPE_CHECKING, Any

from fastmcp.prompts.prompt import Prompt, PromptResult
from fastmcp.resources.resource import Resource, ResourceContent
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools.tool import Tool, ToolResult

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import FunctionPrompt
    from fastmcp.resources.resource import FunctionResource
    from fastmcp.resources.template import FunctionResourceTemplate
    from fastmcp.tools.tool import FunctionTool


@dataclass
class Components:
    """Collection of MCP components."""

    tools: Sequence[Tool] = ()
    resources: Sequence[Resource] = ()
    templates: Sequence[ResourceTemplate] = ()
    prompts: Sequence[Prompt] = ()


@dataclass
class TaskComponents:
    """Collection of function-based components eligible for background task execution.

    Used by get_tasks() to return components for Docket registration.
    All components have a `.fn` attribute pointing to the underlying callable.
    """

    tools: Sequence[FunctionTool] = ()
    resources: Sequence[FunctionResource] = ()
    templates: Sequence[FunctionResourceTemplate] = ()
    prompts: Sequence[FunctionPrompt] = ()


class Provider:
    """Base class for dynamic component providers.

    Subclass and override whichever methods you need. Default implementations
    return empty lists / None, so you only need to implement what your provider
    supports.

    Provider semantics:
        - Return `None` from `get_*` methods to indicate "I don't have it" (search continues)
        - Static components (registered via decorators) always take precedence over providers
        - Providers are queried in registration order; first non-None wins

    Error handling:
        - `list_*` methods: Errors are logged and the provider returns empty (graceful degradation).
          This allows other providers to still contribute their components.
        - Execution methods (`call_tool`, `read_resource`, `render_prompt`): Errors propagate
          with unified handling. ToolError/ResourceError/PromptError pass through; other
          exceptions are wrapped with optional detail masking.
    """

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

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> ToolResult | None:
        """Execute a tool by name.

        Default implementation gets the tool and runs it.
        Override for custom execution logic (e.g., middleware, error handling).

        Returns:
            The ToolResult if found and executed, or None if tool not found.
        """
        tool = await self.get_tool(name)
        if tool is None:
            return None
        return await tool.run(arguments)

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

    async def read_resource(self, uri: str) -> ResourceContent | None:
        """Read a concrete resource by URI.

        Default implementation gets the resource and reads it.
        Override for custom read logic (e.g., middleware, caching).

        Note: This only handles concrete resources. For template-based resources,
        use read_resource_template().

        Returns:
            The ResourceContent if found and read, or None if not found.
        """
        resource = await self.get_resource(uri)
        if resource is None:
            return None
        return await resource._read()

    async def read_resource_template(self, uri: str) -> ResourceContent | None:
        """Read a resource via a matching template.

        Default implementation finds a matching template, creates a resource
        from it, and reads the content.
        Override for custom read logic (e.g., middleware, caching).

        Returns:
            The ResourceContent if a matching template is found and read,
            or None if no template matches.
        """
        template = await self.get_resource_template(uri)
        if template is None:
            return None
        params = template.matches(uri)
        if params is None:
            return None
        resource = await template.create_resource(uri, params)
        return await resource._read()

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

    async def render_prompt(
        self, name: str, arguments: dict[str, Any] | None
    ) -> PromptResult | None:
        """Render a prompt by name.

        Default implementation gets the prompt and renders it.
        Override for custom render logic (e.g., middleware, templating).

        Returns:
            The PromptResult if found and rendered, or None if not found.
        """
        prompt = await self.get_prompt(name)
        if prompt is None:
            return None
        return await prompt._render(arguments)

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
                if isinstance(t, FunctionTool) and t.task_config.mode != "forbidden"
            ],
            resources=[
                r
                for r in all_resources
                if isinstance(r, FunctionResource) and r.task_config.mode != "forbidden"
            ],
            templates=[
                t
                for t in all_templates
                if isinstance(t, FunctionResourceTemplate)
                and t.task_config.mode != "forbidden"
            ],
            prompts=[
                p
                for p in all_prompts
                if isinstance(p, FunctionPrompt) and p.task_config.mode != "forbidden"
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
