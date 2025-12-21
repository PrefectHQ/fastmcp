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
from typing import TYPE_CHECKING, Any

import mcp.types
from mcp.types import AnyUrl

from fastmcp.prompts.prompt import Prompt, PromptResult
from fastmcp.resources.resource import Resource, ResourceContent
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.base import Provider, TaskComponents
from fastmcp.tools.tool import Tool, ToolResult

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
            enabled=tool.enabled,
            task_config=tool.task_config,
        )

    async def _run(
        self, arguments: dict[str, Any]
    ) -> ToolResult | mcp.types.CreateTaskResult:
        """Skip task handling - delegate to run() which calls child middleware.

        The actual underlying tool will check _task_metadata contextvar and
        submit to Docket if appropriate. This wrapper just passes through.
        """
        return await self.run(arguments)

    async def run(
        self, arguments: dict[str, Any]
    ) -> ToolResult | mcp.types.CreateTaskResult:  # type: ignore[override]
        """Delegate to child server's middleware chain.

        This runs BEFORE any backgrounding decision - the actual underlying
        tool will check contextvars and submit to Docket if appropriate.
        """
        return await self._server._call_tool_middleware(self._original_name, arguments)


class FastMCPProviderResource(Resource):
    """Resource that delegates reading to a wrapped server's middleware.

    When `read()` is called, this resource invokes the wrapped server's
    `_read_resource_middleware()` method, ensuring the server's middleware
    chain is executed.
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
            enabled=resource.enabled,
            task_config=resource.task_config,
        )

    async def _read(self) -> ResourceContent | mcp.types.CreateTaskResult:
        """Skip task routing - delegate to read() which calls child middleware.

        The actual underlying resource will check _task_metadata contextvar and
        submit to Docket if appropriate. This wrapper just passes through.
        """
        return await self.read()

    async def read(self) -> ResourceContent | mcp.types.CreateTaskResult:  # type: ignore[override]
        """Delegate to child server's middleware.

        When called from a Docket worker (background task), there's no FastMCP
        context set up, so we create one for the child server.

        Note: The _docket_fn_key contextvar is intentionally NOT updated here.
        The parent set it to the full namespaced key (e.g., data://c/gc/value)
        which is what the function is registered under in Docket. All provider
        layers pass this through unchanged so the eventual resource._read()
        uses the correct Docket lookup key.
        """
        import fastmcp.server.context

        try:
            from fastmcp.server.dependencies import get_context

            get_context()  # Will raise if no context
            result = await self._server._read_resource_middleware(self._original_uri)
            if isinstance(result, mcp.types.CreateTaskResult):
                return result
            return result[0]
        except RuntimeError:
            # No context (e.g., Docket worker) - create one for the child server
            async with fastmcp.server.context.Context(fastmcp=self._server):
                result = await self._server._read_resource_middleware(
                    self._original_uri
                )
                if isinstance(result, mcp.types.CreateTaskResult):
                    return result
                return result[0]


class FastMCPProviderPrompt(Prompt):
    """Prompt that delegates rendering to a wrapped server's middleware.

    When `render()` is called, this prompt invokes the wrapped server's
    `_get_prompt_content_middleware()` method, ensuring the server's middleware
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
    def wrap(cls, server: Any, prompt: Prompt) -> FastMCPProviderPrompt:
        """Wrap a Prompt to delegate rendering to the server's middleware."""
        return cls(
            server=server,
            original_name=prompt.name,
            name=prompt.name,
            description=prompt.description,
            arguments=prompt.arguments,
            tags=prompt.tags,
            enabled=prompt.enabled,
            task_config=prompt.task_config,
        )

    async def _render(
        self, arguments: dict[str, Any] | None = None
    ) -> PromptResult | mcp.types.CreateTaskResult:
        """Skip task routing - delegate to render() which calls child middleware.

        The actual underlying prompt will check _task_metadata contextvar and
        submit to Docket if appropriate. This wrapper just passes through.
        """
        return await self.render(arguments)

    async def render(
        self, arguments: dict[str, Any] | None = None
    ) -> PromptResult | mcp.types.CreateTaskResult:  # type: ignore[override]
        """Delegate to child server's middleware.

        When called from a Docket worker (background task), there's no FastMCP
        context set up, so we create one for the child server.

        Note: The _docket_fn_key contextvar is intentionally NOT updated here.
        The parent set it to the full namespaced name (e.g., c_gc_greet) which
        is what the function is registered under in Docket. All provider layers
        pass this through unchanged so the eventual prompt._render() uses the
        correct Docket lookup key.
        """
        import fastmcp.server.context

        try:
            from fastmcp.server.dependencies import get_context

            get_context()  # Will raise if no context
            result = await self._server._get_prompt_content_middleware(
                self._original_name, arguments
            )
            if isinstance(result, mcp.types.CreateTaskResult):
                return result
            return result
        except RuntimeError:
            # No context (e.g., Docket worker) - create one for the child server
            async with fastmcp.server.context.Context(fastmcp=self._server):
                result = await self._server._get_prompt_content_middleware(
                    self._original_name, arguments
                )
                if isinstance(result, mcp.types.CreateTaskResult):
                    return result
                return result


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
            enabled=template.enabled,
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

    async def _read(
        self, uri: str, params: dict[str, Any]
    ) -> ResourceContent | mcp.types.CreateTaskResult:
        """Delegate to child server's middleware.

        Skips task routing at this layer - the child's template._read() will
        check _task_metadata contextvar and submit to Docket if appropriate.

        Sets _docket_fn_key to self.uri_template (the transformed pattern) so that
        when the child template's _read() submits to Docket, it uses the correct
        key that matches what was registered via TransformingProvider.get_tasks().

        Only sets _docket_fn_key if not already set - in nested mounts, the
        outermost wrapper sets the key and inner wrappers preserve it.
        """
        import fastmcp.server.context
        from fastmcp.server.dependencies import _docket_fn_key

        # Expand the original template with params to get internal URI
        original_uri = _expand_uri_template(self._original_uri_template or "", params)

        # Set _docket_fn_key to the template pattern, but only if the current
        # value isn't already a template pattern (contains '{').
        # - Server sets concrete URI (e.g., "item://c/gc/42") - no '{', override it
        # - Outer wrapper sets pattern (e.g., "item://c/gc/{id}") - has '{', keep it
        # In nested mounts (parent→child→grandchild), the outermost wrapper
        # has the fully-transformed pattern that matches Docket registration.
        existing_key = _docket_fn_key.get()
        key_token = None
        if not existing_key or "{" not in existing_key:
            key_token = _docket_fn_key.set(self.uri_template)
        try:
            try:
                from fastmcp.server.dependencies import get_context

                get_context()  # Will raise if no context
                result = await self._server._read_resource_middleware(original_uri)
                if isinstance(result, mcp.types.CreateTaskResult):
                    return result
                return result[0]
            except RuntimeError:
                # No context (e.g., Docket worker) - create one for the child server
                async with fastmcp.server.context.Context(fastmcp=self._server):
                    result = await self._server._read_resource_middleware(original_uri)
                    if isinstance(result, mcp.types.CreateTaskResult):
                        return result
                    return result[0]
        finally:
            if key_token is not None:
                _docket_fn_key.reset(key_token)

    async def read(self, arguments: dict[str, Any]) -> str | bytes:
        """Read the resource content for background task execution.

        Creates a resource from this template and reads its content.
        This method is called by Docket during background task execution.
        """
        # Expand the original template with arguments to get internal URI
        original_uri = _expand_uri_template(
            self._original_uri_template or "", arguments
        )

        # Create and read the resource
        resource = FastMCPProviderResource(
            server=self._server,
            original_uri=original_uri,
            uri=AnyUrl(original_uri),
            name=self.name,
            description=self.description,
            mime_type=self.mime_type,
        )
        result = await resource.read()

        # Return raw content (str or bytes)
        if hasattr(result, "content"):
            return result.content  # type: ignore[return-value]
        return result  # type: ignore[return-value]

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
        raw_tools = await self.server._list_tools_middleware()
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
        raw_resources = await self.server._list_resources_middleware()
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
        raw_templates = await self.server._list_resource_templates_middleware()
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
        raw_prompts = await self.server._list_prompts_middleware()
        return [FastMCPProviderPrompt.wrap(self.server, p) for p in raw_prompts]

    async def get_prompt(self, name: str) -> Prompt | None:
        """Get a prompt by name as a FastMCPProviderPrompt."""
        prompts = await self.list_prompts()
        return next((p for p in prompts if p.name == name), None)

    # -------------------------------------------------------------------------
    # Task registration
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> TaskComponents:
        """Return task-eligible components from the mounted server.

        Returns the child's ACTUAL components (not wrapped) so their actual
        functions get registered with Docket. TransformingProvider.get_tasks()
        handles namespace transformation of keys.

        Accesses managers directly to avoid triggering middleware during startup.
        """
        # Return child's actual components - their .fn gets registered with Docket
        # TransformingProvider.get_tasks() transforms keys to include namespace
        tools: list[Tool] = [
            t
            for t in self.server._tool_manager._tools.values()
            if t.task_config.supports_tasks()
        ]
        resources: list[Resource] = [
            r
            for r in self.server._resource_manager._resources.values()
            if r.task_config.supports_tasks()
        ]
        templates: list[ResourceTemplate] = [
            t
            for t in self.server._resource_manager._templates.values()
            if t.task_config.supports_tasks()
        ]
        prompts: list[Prompt] = [
            p
            for p in self.server._prompt_manager._prompts.values()
            if p.task_config.supports_tasks()
        ]

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
