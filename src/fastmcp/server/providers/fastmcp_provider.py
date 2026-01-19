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
from fastmcp.server.telemetry import delegate_span
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.versions import VersionSpec

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
            version=tool.version,
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
        # Pass exact version so child executes the correct version
        version = VersionSpec(eq=self.version) if self.version else None

        with delegate_span(
            self._original_name or "", "FastMCPProvider", self._original_name or ""
        ):
            return await self._server.call_tool(
                self._original_name, arguments, version=version, task_meta=task_meta
            )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Delegate to child server's call_tool() without task_meta.

        This is called when the tool is used within a TransformedTool
        forwarding function or other contexts where task_meta is not available.
        """
        # Pass exact version so child executes the correct version
        version = VersionSpec(eq=self.version) if self.version else None

        result = await self._server.call_tool(
            self._original_name, arguments, version=version
        )
        # Result from call_tool should always be ToolResult when no task_meta
        if isinstance(result, mcp.types.CreateTaskResult):
            raise RuntimeError(
                "Unexpected CreateTaskResult from call_tool without task_meta"
            )
        return result

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "FastMCPProvider",
            "fastmcp.delegate.original_name": self._original_name,
        }


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
            version=resource.version,
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
        # Pass exact version so child reads the correct version
        version = VersionSpec(eq=self.version) if self.version else None

        with delegate_span(
            self._original_uri or "", "FastMCPProvider", self._original_uri or ""
        ):
            return await self._server.read_resource(
                self._original_uri, version=version, task_meta=task_meta
            )

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "FastMCPProvider",
            "fastmcp.delegate.original_uri": self._original_uri,
        }


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
            version=prompt.version,
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
        # Pass exact version so child renders the correct version
        version = VersionSpec(eq=self.version) if self.version else None

        with delegate_span(
            self._original_name or "", "FastMCPProvider", self._original_name or ""
        ):
            return await self._server.render_prompt(
                self._original_name, arguments, version=version, task_meta=task_meta
            )

    async def render(self, arguments: dict[str, Any] | None = None) -> PromptResult:
        """Delegate to child server's render_prompt() without task_meta.

        This is called when the prompt is used within a transformed context
        or other contexts where task_meta is not available.
        """
        # Pass exact version so child renders the correct version
        version = VersionSpec(eq=self.version) if self.version else None

        result = await self._server.render_prompt(
            self._original_name, arguments, version=version
        )
        # Result from render_prompt should always be PromptResult when no task_meta
        if isinstance(result, mcp.types.CreateTaskResult):
            raise RuntimeError(
                "Unexpected CreateTaskResult from render_prompt without task_meta"
            )
        return result

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "FastMCPProvider",
            "fastmcp.delegate.original_name": self._original_name,
        }


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
            version=template.version,
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

        # Pass exact version so child reads the correct version
        version = VersionSpec(eq=self.version) if self.version else None

        with delegate_span(
            original_uri, "FastMCPProvider", self._original_uri_template or ""
        ):
            return await self._server.read_resource(
                original_uri, version=version, task_meta=task_meta
            )

    async def read(self, arguments: dict[str, Any]) -> str | bytes | ResourceResult:
        """Read the resource content for background task execution.

        Reads the resource via the wrapped server and returns the ResourceResult.
        This method is called by Docket during background task execution.
        """
        # Expand the original template with arguments to get internal URI
        original_uri = _expand_uri_template(
            self._original_uri_template or "", arguments
        )

        # Pass exact version so child reads the correct version
        version = VersionSpec(eq=self.version) if self.version else None

        # Read from the wrapped server
        result = await self._server.read_resource(original_uri, version=version)
        if isinstance(result, mcp.types.CreateTaskResult):
            raise RuntimeError("Unexpected CreateTaskResult during Docket execution")

        return result

    def register_with_docket(self, docket: Docket) -> None:
        """No-op: the child's actual template is registered via get_tasks()."""

    async def add_to_docket(
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

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "FastMCPProvider",
            "fastmcp.delegate.original_uri_template": self._original_uri_template,
        }


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
        from fastmcp.server.transforms import Namespace
        provider = FastMCPProvider(sub)
        provider.add_transform(Namespace("sub"))
        main.add_provider(provider)
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

    async def _list_tools(self) -> Sequence[Tool]:
        """List all tools from the mounted server as FastMCPProviderTools.

        Runs the mounted server's middleware so filtering/transformation applies.
        Wraps each tool as a FastMCPProviderTool that delegates execution to
        the nested server's middleware.
        """
        raw_tools = await self.server.list_tools()
        return [FastMCPProviderTool.wrap(self.server, t) for t in raw_tools]

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name as a FastMCPProviderTool.

        Passes the full VersionSpec to the nested server, which handles both
        exact version matching and range filtering. Uses get_tool to ensure
        the nested server's transforms are applied.
        """
        raw_tool = await self.server.get_tool(name, version)
        if raw_tool is None:
            return None
        return FastMCPProviderTool.wrap(self.server, raw_tool)

    # -------------------------------------------------------------------------
    # Resource methods
    # -------------------------------------------------------------------------

    async def _list_resources(self) -> Sequence[Resource]:
        """List all resources from the mounted server as FastMCPProviderResources.

        Runs the mounted server's middleware so filtering/transformation applies.
        Wraps each resource as a FastMCPProviderResource that delegates reading
        to the nested server's middleware.
        """
        raw_resources = await self.server.list_resources()
        return [FastMCPProviderResource.wrap(self.server, r) for r in raw_resources]

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get a concrete resource by URI as a FastMCPProviderResource.

        Passes the full VersionSpec to the nested server, which handles both
        exact version matching and range filtering. Uses get_resource to ensure
        the nested server's transforms are applied.
        """
        raw_resource = await self.server.get_resource(uri, version)
        if raw_resource is None:
            return None
        return FastMCPProviderResource.wrap(self.server, raw_resource)

    # -------------------------------------------------------------------------
    # Resource template methods
    # -------------------------------------------------------------------------

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List all resource templates from the mounted server.

        Runs the mounted server's middleware so filtering/transformation applies.
        Returns FastMCPProviderResourceTemplate instances that create
        FastMCPProviderResources when materialized.
        """
        raw_templates = await self.server.list_resource_templates()
        return [
            FastMCPProviderResourceTemplate.wrap(self.server, t) for t in raw_templates
        ]

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get a resource template that matches the given URI.

        Passes the full VersionSpec to the nested server, which handles both
        exact version matching and range filtering. Uses get_resource_template
        to ensure the nested server's transforms are applied.
        """
        raw_template = await self.server.get_resource_template(uri, version)
        if raw_template is None:
            return None
        return FastMCPProviderResourceTemplate.wrap(self.server, raw_template)

    # -------------------------------------------------------------------------
    # Prompt methods
    # -------------------------------------------------------------------------

    async def _list_prompts(self) -> Sequence[Prompt]:
        """List all prompts from the mounted server as FastMCPProviderPrompts.

        Runs the mounted server's middleware so filtering/transformation applies.
        Returns FastMCPProviderPrompt instances that delegate rendering to the
        wrapped server's middleware.
        """
        raw_prompts = await self.server.list_prompts()
        return [FastMCPProviderPrompt.wrap(self.server, p) for p in raw_prompts]

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get a prompt by name as a FastMCPProviderPrompt.

        Passes the full VersionSpec to the nested server, which handles both
        exact version matching and range filtering. Uses get_prompt to ensure
        the nested server's transforms are applied.
        """
        raw_prompt = await self.server.get_prompt(name, version)
        if raw_prompt is None:
            return None
        return FastMCPProviderPrompt.wrap(self.server, raw_prompt)

    # -------------------------------------------------------------------------
    # Task registration
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Return task-eligible components from the mounted server.

        Returns the child's ACTUAL components (not wrapped) so their actual
        functions get registered with Docket. Gets components with child
        server's transforms applied, then applies this provider's transforms
        for correct registration keys.
        """
        # Get tasks with child server's transforms already applied
        components = list(await self.server.get_tasks())

        # Separate by type for this provider's transform application
        tools = [c for c in components if isinstance(c, Tool)]
        resources = [c for c in components if isinstance(c, Resource)]
        templates = [c for c in components if isinstance(c, ResourceTemplate)]
        prompts = [c for c in components if isinstance(c, Prompt)]

        # Apply this provider's transforms sequentially
        for transform in self.transforms:
            tools = await transform.list_tools(tools)
            resources = await transform.list_resources(resources)
            templates = await transform.list_resource_templates(templates)
            prompts = await transform.list_prompts(prompts)

        # Filter to only task-eligible components (same as base Provider)
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
        """Start the mounted server's user lifespan.

        This starts only the wrapped server's user-defined lifespan, NOT its
        full _lifespan_manager() (which includes Docket). The parent server's
        Docket handles all background tasks.
        """
        async with self.server._lifespan(self.server):
            yield
