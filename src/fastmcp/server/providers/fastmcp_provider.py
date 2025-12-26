"""FastMCPProvider for wrapping FastMCP servers as providers.

This module provides the `FastMCPProvider` class that wraps a FastMCP server
and exposes its components through the Provider interface.

It also provides FastMCPProvider* component classes that delegate execution to
the wrapped server's middleware, ensuring middleware runs when components are
executed.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, overload

import mcp.types
from mcp.types import AnyUrl

from fastmcp.prompts.prompt import Prompt, PromptResult
from fastmcp.resources.resource import Resource, ResourceResult
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.base import Provider
from fastmcp.server.tasks.config import TaskMeta
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.components import FastMCPComponent

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

    from fastmcp.server.server import FastMCP


def _expand_uri_template(template: str, params: dict[str, Any]) -> str:
    """Expand a URI template with parameters.

    Simple implementation that handles {name} style placeholders.
    """
    result = template
    for key, value in params.items():
        result = re.sub(rf"\{{{key}\}}", str(value), result)
    return result


# -----------------------------------------------------------------------------
# FastMCPProvider component classes
# -----------------------------------------------------------------------------


class FastMCPProviderTool(Tool):
    """Tool that delegates execution to a wrapped server's middleware.

    When `run()` is called, this tool invokes the wrapped server's
    `_call_tool_middleware()` method, ensuring the server's middleware
    chain is executed.
    """

    _server: Any = None  # FastMCP, but Any to avoid circular import
    _original_name: str | None = None

    def __init__(
        self,
        server: Any,
        original_name: str,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._server = server
        self._original_name = original_name

    @classmethod
    def wrap(cls, server: Any, tool: Tool) -> FastMCPProviderTool:
        """Wrap a Tool to delegate execution to the server's middleware."""
        return cls(
            server=server,
            original_name=tool.name,
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            output_schema=tool.output_schema,
            tags=tool.tags,
            annotations=tool.annotations,
            task_config=tool.task_config,
        )

    @overload
    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: None = None,
    ) -> ToolResult: ...

    @overload
    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: TaskMeta | None = None,
    ) -> ToolResult | mcp.types.CreateTaskResult:
        """Delegate to child server's call_tool() with task_meta.

        Passes task_meta through to the child server so it can handle
        backgrounding appropriately. fn_key is already set by the parent
        server before calling this method.
        """
        return await self._server.call_tool(
            self._original_name, arguments, task_meta=task_meta
        )

    async def run(
        self, arguments: dict[str, Any]
    ) -> ToolResult | mcp.types.CreateTaskResult:  # type: ignore[override]
        """Not implemented - use _run() which delegates to child server.

        FastMCPProviderTool._run() handles all execution by delegating
        to the child server's call_tool() with task_meta.
        """
        raise NotImplementedError(
            "FastMCPProviderTool.run() should not be called directly. "
            "Use _run() which delegates to the child server's call_tool()."
        )


class FastMCPProviderResource(Resource):
    """Resource that delegates reading to a wrapped server's read_resource().

    When `read()` is called, this resource invokes the wrapped server's
    `read_resource()` method, ensuring the server's middleware chain is executed.
    """

    _server: Any = None  # FastMCP, but Any to avoid circular import
    _original_uri: str | None = None

    def __init__(
        self,
        server: Any,
        original_uri: str,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._server = server
        self._original_uri = original_uri

    @classmethod
    def wrap(cls, server: Any, resource: Resource) -> FastMCPProviderResource:
        """Wrap a Resource to delegate reading to the server's middleware."""
        return cls(
            server=server,
            original_uri=str(resource.uri),
            uri=resource.uri,
            name=resource.name,
            description=resource.description,
            mime_type=resource.mime_type,
            tags=resource.tags,
            annotations=resource.annotations,
            task_config=resource.task_config,
        )

    @overload
    async def _read(self, task_meta: None = None) -> ResourceResult: ...

    @overload
    async def _read(self, task_meta: TaskMeta) -> mcp.types.CreateTaskResult: ...

    async def _read(
        self, task_meta: TaskMeta | None = None
    ) -> ResourceResult | mcp.types.CreateTaskResult:
        """Delegate to child server's read_resource() with task_meta.

        Passes task_meta through to the child server so it can handle
        backgrounding appropriately. fn_key is already set by the parent
        server before calling this method.
        """
        return await self._server.read_resource(self._original_uri, task_meta=task_meta)


class FastMCPProviderPrompt(Prompt):
    """Prompt that delegates rendering to a wrapped server's render_prompt().

    When `render()` is called, this prompt invokes the wrapped server's
    `render_prompt()` method, ensuring the server's middleware chain is executed.
    """

    _server: Any = None  # FastMCP, but Any to avoid circular import
    _original_name: str | None = None

    def __init__(
        self,
        server: Any,
        original_name: str,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._server = server
        self._original_name = original_name

    @classmethod
    def wrap(cls, server: Any, prompt: Prompt) -> FastMCPProviderPrompt:
        """Wrap a Prompt to delegate rendering to the server's middleware."""
        return cls(
            server=server,
            original_name=prompt.name,
            name=prompt.name,
            description=prompt.description,
            arguments=prompt.arguments,
            tags=prompt.tags,
            task_config=prompt.task_config,
        )

    @overload
    async def _render(
        self,
        arguments: dict[str, Any] | None = None,
        task_meta: None = None,
    ) -> PromptResult: ...

    @overload
    async def _render(
        self,
        arguments: dict[str, Any] | None,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def _render(
        self,
        arguments: dict[str, Any] | None = None,
        task_meta: TaskMeta | None = None,
    ) -> PromptResult | mcp.types.CreateTaskResult:
        """Delegate to child server's render_prompt() with task_meta.

        Passes task_meta through to the child server so it can handle
        backgrounding appropriately. fn_key is already set by the parent
        server before calling this method.
        """
        return await self._server.render_prompt(
            self._original_name, arguments, task_meta=task_meta
        )

    async def render(
        self, arguments: dict[str, Any] | None = None
    ) -> PromptResult | mcp.types.CreateTaskResult:  # type: ignore[override]
        """Not implemented - use _render() which delegates to child server.

        FastMCPProviderPrompt._render() handles all execution by delegating
        to the child server's render_prompt() with task_meta.
        """
        raise NotImplementedError(
            "FastMCPProviderPrompt.render() should not be called directly. "
            "Use _render() which delegates to the child server's render_prompt()."
        )


class FastMCPProviderResourceTemplate(ResourceTemplate):
    """Resource template that creates FastMCPProviderResources.

    When `create_resource()` is called, this template creates a
    FastMCPProviderResource that will invoke the wrapped server's middleware
    when read.
    """

    _server: Any = None  # FastMCP, but Any to avoid circular import
    _original_uri_template: str | None = None

    def __init__(
        self,
        server: Any,
        original_uri_template: str,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._server = server
        self._original_uri_template = original_uri_template

    @classmethod
    def wrap(
        cls, server: Any, template: ResourceTemplate
    ) -> FastMCPProviderResourceTemplate:
        """Wrap a ResourceTemplate to create FastMCPProviderResources."""
        return cls(
            server=server,
            original_uri_template=template.uri_template,
            uri_template=template.uri_template,
            name=template.name,
            description=template.description,
            mime_type=template.mime_type,
            parameters=template.parameters,
            tags=template.tags,
            annotations=template.annotations,
            task_config=template.task_config,
        )

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a FastMCPProviderResource for the given URI.

        The `uri` is the external/transformed URI (e.g., with namespace prefix).
        We use `_original_uri_template` with `params` to construct the internal
        URI that the nested server understands.
        """
        # Expand the original template with params to get internal URI
        original_uri = _expand_uri_template(self._original_uri_template or "", params)
        return FastMCPProviderResource(
            server=self._server,
            original_uri=original_uri,
            uri=AnyUrl(uri),
            name=self.name,
            description=self.description,
            mime_type=self.mime_type,
        )

    @overload
    async def _read(
        self, uri: str, params: dict[str, Any], task_meta: None = None
    ) -> ResourceResult: ...

    @overload
    async def _read(
        self, uri: str, params: dict[str, Any], task_meta: TaskMeta
    ) -> mcp.types.CreateTaskResult: ...

    async def _read(
        self, uri: str, params: dict[str, Any], task_meta: TaskMeta | None = None
    ) -> ResourceResult | mcp.types.CreateTaskResult:
        """Delegate to child server's read_resource() with task_meta.

        Passes task_meta through to the child server so it can handle
        backgrounding appropriately. fn_key is already set by the parent
        server before calling this method.
        """
        # Expand the original template with params to get internal URI
        original_uri = _expand_uri_template(self._original_uri_template or "", params)

        return await self._server.read_resource(original_uri, task_meta=task_meta)

    async def read(self, arguments: dict[str, Any]) -> str | bytes | ResourceResult:
        """Read the resource content for background task execution.

        Reads the resource via the wrapped server and returns the ResourceResult.
        This method is called by Docket during background task execution.
        """
        # Expand the original template with arguments to get internal URI
        original_uri = _expand_uri_template(
            self._original_uri_template or "", arguments
        )

        # Read from the wrapped server
        result = await self._server.read_resource(original_uri)
        if isinstance(result, mcp.types.CreateTaskResult):
            raise RuntimeError("Unexpected CreateTaskResult during Docket execution")

        return result

    def register_with_docket(self, docket: Docket) -> None:
        """No-op: the child's actual template is registered via get_tasks()."""

    async def add_to_docket(  # type: ignore[override]
        self,
        docket: Docket,
        params: dict[str, Any],
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this template for background execution via docket.

        The child's FunctionResourceTemplate.fn is registered (via get_tasks),
        and it expects splatted **kwargs, so we splat params here.
        """
        lookup_key = fn_key or self.key
        if task_key:
            kwargs["key"] = task_key
        return await docket.add(lookup_key, **kwargs)(**params)


# -----------------------------------------------------------------------------
# FastMCPProvider
# -----------------------------------------------------------------------------


class FastMCPProvider(Provider):
    """Provider that wraps a FastMCP server.

    This provider enables mounting one FastMCP server onto another, exposing
    the mounted server's tools, resources, and prompts through the parent
    server.

    Components returned by this provider are wrapped in FastMCPProvider*
    classes that delegate execution to the wrapped server's middleware chain.
    This ensures middleware runs when components are executed.

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
        """List all tools from the mounted server as FastMCPProviderTools.

        Calls the nested server's middleware to list tools, then wraps
        each tool as a FastMCPProviderTool that delegates execution to the
        nested server's middleware.
        """
        raw_tools = await self.server.get_tools(run_middleware=True)
        return [FastMCPProviderTool.wrap(self.server, t) for t in raw_tools]

    async def get_tool(self, name: str) -> Tool | None:
        """Get a tool by name as a FastMCPProviderTool."""
        tools = await self.list_tools()
        return next((t for t in tools if t.name == name), None)

    # -------------------------------------------------------------------------
    # Resource methods
    # -------------------------------------------------------------------------

    async def list_resources(self) -> Sequence[Resource]:
        """List all resources from the mounted server as FastMCPProviderResources.

        Calls the nested server's middleware to list resources, then wraps
        each resource as a FastMCPProviderResource that delegates reading to the
        nested server's middleware.
        """
        raw_resources = await self.server.get_resources(run_middleware=True)
        return [FastMCPProviderResource.wrap(self.server, r) for r in raw_resources]

    async def get_resource(self, uri: str) -> Resource | None:
        """Get a concrete resource by URI as a FastMCPProviderResource."""
        resources = await self.list_resources()
        return next((r for r in resources if str(r.uri) == uri), None)

    # -------------------------------------------------------------------------
    # Resource template methods
    # -------------------------------------------------------------------------

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List all resource templates from the mounted server.

        Returns FastMCPProviderResourceTemplate instances that create
        FastMCPProviderResources when materialized.
        """
        raw_templates = await self.server.get_resource_templates(run_middleware=True)
        return [
            FastMCPProviderResourceTemplate.wrap(self.server, t) for t in raw_templates
        ]

    async def get_resource_template(self, uri: str) -> ResourceTemplate | None:
        """Get a resource template that matches the given URI."""
        templates = await self.list_resource_templates()
        for template in templates:
            if template.matches(uri) is not None:
                return template
        return None

    # -------------------------------------------------------------------------
    # Prompt methods
    # -------------------------------------------------------------------------

    async def list_prompts(self) -> Sequence[Prompt]:
        """List all prompts from the mounted server as FastMCPProviderPrompts.

        Returns FastMCPProviderPrompt instances that delegate rendering to the
        wrapped server's middleware.
        """
        raw_prompts = await self.server.get_prompts(run_middleware=True)
        return [FastMCPProviderPrompt.wrap(self.server, p) for p in raw_prompts]

    async def get_prompt(self, name: str) -> Prompt | None:
        """Get a prompt by name as a FastMCPProviderPrompt."""
        prompts = await self.list_prompts()
        return next((p for p in prompts if p.name == name), None)

    # -------------------------------------------------------------------------
    # Task registration
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Return task-eligible components from the mounted server.

        Returns the child's ACTUAL components (not wrapped) so their actual
        functions get registered with Docket. TransformingProvider.get_tasks()
        handles namespace transformation of keys.

        Iterates through all providers in the wrapped server (including its
        LocalProvider) to collect task-eligible components.
        """
        components: list[FastMCPComponent] = []
        for provider in self.server._providers:
            components.extend(await provider.get_tasks())
        return components

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
