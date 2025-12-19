"""FastMCPProvider for wrapping FastMCP servers as providers.

This module provides the `FastMCPProvider` class that wraps a FastMCP server
and exposes its components through the Provider interface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import NotFoundError
from fastmcp.prompts.prompt import Prompt, PromptResult
from fastmcp.resources.resource import Resource, ResourceContent
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.base import Provider, TaskComponents
from fastmcp.tools.tool import Tool, ToolResult

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import FunctionPrompt
    from fastmcp.resources.resource import FunctionResource
    from fastmcp.resources.template import FunctionResourceTemplate
    from fastmcp.server.server import FastMCP
    from fastmcp.tools.tool import FunctionTool


class FastMCPProvider(Provider):
    """Provider that wraps a FastMCP server.

    This provider enables mounting one FastMCP server onto another, exposing
    the mounted server's tools, resources, and prompts through the parent
    server.

    Execution methods (`call_tool`, `read_resource`, `render_prompt`) invoke
    the mounted server's middleware chain.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.providers import FastMCPProvider

        main = FastMCP("Main")
        sub = FastMCP("Sub")

        @sub.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # Mount directly - tools accessible by original names
        main.add_provider(FastMCPProvider(sub))

        # Or with namespace
        main.add_provider(FastMCPProvider(sub).with_namespace("sub"))
        ```

    Note:
        Normally you would use `FastMCP.mount()` which handles proxy conversion
        and creates the provider with namespace automatically.
    """

    def __init__(self, server: FastMCP[Any]):
        """Initialize a FastMCPProvider.

        Args:
            server: The FastMCP server to wrap.
        """
        super().__init__()
        self.server = server

    # -------------------------------------------------------------------------
    # Tool methods
    # -------------------------------------------------------------------------

    async def list_tools(self) -> Sequence[Tool]:
        """List all tools from the mounted server."""
        return await self.server._list_tools_middleware()

    async def get_tool(self, name: str) -> Tool | None:
        """Get a tool by name."""
        tools = await self.list_tools()
        return next((t for t in tools if t.name == name), None)

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> ToolResult | None:
        """Execute a tool through the mounted server's middleware chain."""
        return await self.server._call_tool_middleware(name, arguments)

    # -------------------------------------------------------------------------
    # Resource methods
    # -------------------------------------------------------------------------

    async def list_resources(self) -> Sequence[Resource]:
        """List all resources from the mounted server."""
        return await self.server._list_resources_middleware()

    async def get_resource(self, uri: str) -> Resource | None:
        """Get a concrete resource by URI."""
        resources = await self.list_resources()
        return next((r for r in resources if str(r.uri) == uri), None)

    async def read_resource(self, uri: str) -> ResourceContent | None:
        """Read a resource through the mounted server's middleware chain."""
        try:
            contents = await self.server._read_resource_middleware(uri)
            return contents[0] if contents else None
        except NotFoundError:
            return None

    # -------------------------------------------------------------------------
    # Resource template methods
    # -------------------------------------------------------------------------

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List all resource templates from the mounted server."""
        return await self.server._list_resource_templates_middleware()

    async def get_resource_template(self, uri: str) -> ResourceTemplate | None:
        """Get a resource template that matches the given URI."""
        templates = await self.list_resource_templates()
        for template in templates:
            if template.matches(uri) is not None:
                return template
        return None

    async def read_resource_template(self, uri: str) -> ResourceContent | None:
        """Read a resource via a matching template through the mounted server."""
        # The server's middleware handles template resolution
        return await self.read_resource(uri)

    # -------------------------------------------------------------------------
    # Prompt methods
    # -------------------------------------------------------------------------

    async def list_prompts(self) -> Sequence[Prompt]:
        """List all prompts from the mounted server."""
        return await self.server._list_prompts_middleware()

    async def get_prompt(self, name: str) -> Prompt | None:
        """Get a prompt by name."""
        prompts = await self.list_prompts()
        return next((p for p in prompts if p.name == name), None)

    async def render_prompt(
        self, name: str, arguments: dict[str, Any] | None
    ) -> PromptResult | None:
        """Render a prompt through the mounted server's middleware chain."""
        return await self.server._get_prompt_content_middleware(name, arguments)

    # -------------------------------------------------------------------------
    # Task registration
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> TaskComponents:
        """Return task-eligible components from the mounted server.

        Accesses the wrapped server's managers directly to avoid triggering
        middleware during registration. Also recursively collects tasks from
        nested providers.
        """
        from fastmcp.prompts.prompt import FunctionPrompt
        from fastmcp.resources.resource import FunctionResource
        from fastmcp.resources.template import FunctionResourceTemplate
        from fastmcp.tools.tool import FunctionTool

        tools: list[FunctionTool] = []
        resources: list[FunctionResource] = []
        templates: list[FunctionResourceTemplate] = []
        prompts: list[FunctionPrompt] = []

        # Direct manager access (bypasses middleware)
        for tool in self.server._tool_manager._tools.values():
            if isinstance(tool, FunctionTool) and tool.task_config.mode != "forbidden":
                tools.append(tool)

        for resource in self.server._resource_manager._resources.values():
            if (
                isinstance(resource, FunctionResource)
                and resource.task_config.mode != "forbidden"
            ):
                resources.append(resource)

        for template in self.server._resource_manager._templates.values():
            if (
                isinstance(template, FunctionResourceTemplate)
                and template.task_config.mode != "forbidden"
            ):
                templates.append(template)

        for prompt in self.server._prompt_manager._prompts.values():
            if (
                isinstance(prompt, FunctionPrompt)
                and prompt.task_config.mode != "forbidden"
            ):
                prompts.append(prompt)

        # Recursively get tasks from nested providers
        for provider in self.server._providers:
            nested = await provider.get_tasks()
            tools.extend(nested.tools)
            resources.extend(nested.resources)
            templates.extend(nested.templates)
            prompts.extend(nested.prompts)

        return TaskComponents(
            tools=tools, resources=resources, templates=templates, prompts=prompts
        )

    # -------------------------------------------------------------------------
    # Lifecycle methods
    # -------------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Start the mounted server's user lifespan.

        This starts only the wrapped server's user-defined lifespan, NOT its
        full _lifespan_manager() (which includes Docket). The parent server's
        Docket handles all background tasks.
        """
        async with self.server._lifespan(self.server):
            yield
