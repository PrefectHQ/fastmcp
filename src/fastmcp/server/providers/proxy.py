"""ProxyProvider for proxying to remote MCP servers.

This module provides the `ProxyProvider` class that proxies components from
a remote MCP server via a client factory. It also provides proxy component
classes that forward execution to remote servers.
"""

from __future__ import annotations

import base64
import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import mcp.types
from mcp import ServerSession
from mcp.client.session import ClientSession
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.shared.exceptions import McpError
from mcp.types import (
    METHOD_NOT_FOUND,
    BlobResourceContents,
    ElicitRequestFormParams,
    TextResourceContents,
)
from pydantic.networks import AnyUrl

from fastmcp.client.client import Client, FastMCP1Server
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.logging import LogMessage
from fastmcp.client.roots import RootsList
from fastmcp.client.telemetry import client_span
from fastmcp.client.transports import ClientTransportT
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.mcp_config import MCPConfig
from fastmcp.prompts import Message, Prompt, PromptResult
from fastmcp.prompts.prompt import PromptArgument
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.resources.resource import ResourceContent, ResourceResult
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.base import Provider
from fastmcp.server.server import FastMCP
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.components import FastMCPComponent, get_fastmcp_metadata
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.client.transports import ClientTransport

logger = get_logger(__name__)

# Type alias for client factory functions
ClientFactoryT = Callable[[], Client] | Callable[[], Awaitable[Client]]


# -----------------------------------------------------------------------------
# Proxy Component Classes
# -----------------------------------------------------------------------------


class ProxyTool(Tool):
    """A Tool that represents and executes a tool on a remote server."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")
    _backend_name: str | None = None

    def __init__(self, client_factory: ClientFactoryT, **kwargs: Any):
        super().__init__(**kwargs)
        self._client_factory = client_factory

    async def _get_client(self) -> Client:
        """Gets a client instance by calling the sync or async factory."""
        client = self._client_factory()
        if inspect.isawaitable(client):
            client = await client
        return client

    def model_copy(self, **kwargs: Any) -> ProxyTool:
        """Override to preserve _backend_name when name changes."""
        update = kwargs.get("update", {})
        if "name" in update and self._backend_name is None:
            # First time name is being changed, preserve original for backend calls
            update = {**update, "_backend_name": self.name}
            kwargs["update"] = update
        return super().model_copy(**kwargs)

    @classmethod
    def from_mcp_tool(
        cls, client_factory: ClientFactoryT, mcp_tool: mcp.types.Tool
    ) -> ProxyTool:
        """Factory method to create a ProxyTool from a raw MCP tool schema."""
        return cls(
            client_factory=client_factory,
            name=mcp_tool.name,
            title=mcp_tool.title,
            description=mcp_tool.description,
            parameters=mcp_tool.inputSchema,
            annotations=mcp_tool.annotations,
            output_schema=mcp_tool.outputSchema,
            icons=mcp_tool.icons,
            meta=mcp_tool.meta,
            tags=get_fastmcp_metadata(mcp_tool.meta).get("tags", []),
        )

    async def run(
        self,
        arguments: dict[str, Any],
        context: Context | None = None,
    ) -> ToolResult:
        """Executes the tool by making a call through the client."""
        backend_name = self._backend_name or self.name
        with client_span(
            f"tools/call {backend_name}", "tools/call", backend_name
        ) as span:
            span.set_attribute("fastmcp.provider.type", "ProxyProvider")
            client = await self._get_client()
            async with client:
                ctx = context or get_context()
                # Build meta dict from request context
                meta: dict[str, Any] | None = None
                if hasattr(ctx, "request_context"):
                    req_ctx = ctx.request_context
                    # Start with existing meta if present
                    if hasattr(req_ctx, "meta") and req_ctx.meta:
                        meta = dict(req_ctx.meta)
                    # Add task metadata if this is a task request
                    if (
                        hasattr(req_ctx, "experimental")
                        and hasattr(req_ctx.experimental, "is_task")
                        and req_ctx.experimental.is_task
                    ):
                        task_metadata = req_ctx.experimental.task_metadata
                        if task_metadata:
                            meta = meta or {}
                            meta["modelcontextprotocol.io/task"] = (
                                task_metadata.model_dump(exclude_none=True)
                            )

                result = await client.call_tool_mcp(
                    name=backend_name, arguments=arguments, meta=meta
                )
            if result.isError:
                raise ToolError(cast(mcp.types.TextContent, result.content[0]).text)
            # Preserve backend's meta (includes task metadata for background tasks)
            return ToolResult(
                content=result.content,
                structured_content=result.structuredContent,
                meta=result.meta,
            )

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "ProxyProvider",
            "fastmcp.proxy.backend_name": self._backend_name,
        }


class ProxyResource(Resource):
    """A Resource that represents and reads a resource from a remote server."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")
    _cached_content: ResourceResult | None = None
    _backend_uri: str | None = None

    def __init__(
        self,
        client_factory: ClientFactoryT,
        *,
        _cached_content: ResourceResult | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._client_factory = client_factory
        self._cached_content = _cached_content

    async def _get_client(self) -> Client:
        """Gets a client instance by calling the sync or async factory."""
        client = self._client_factory()
        if inspect.isawaitable(client):
            client = await client
        return client

    def model_copy(self, **kwargs: Any) -> ProxyResource:
        """Override to preserve _backend_uri when uri changes."""
        update = kwargs.get("update", {})
        if "uri" in update and self._backend_uri is None:
            # First time uri is being changed, preserve original for backend calls
            update = {**update, "_backend_uri": str(self.uri)}
            kwargs["update"] = update
        return super().model_copy(**kwargs)

    @classmethod
    def from_mcp_resource(
        cls,
        client_factory: ClientFactoryT,
        mcp_resource: mcp.types.Resource,
    ) -> ProxyResource:
        """Factory method to create a ProxyResource from a raw MCP resource schema."""

        return cls(
            client_factory=client_factory,
            uri=mcp_resource.uri,
            name=mcp_resource.name,
            title=mcp_resource.title,
            description=mcp_resource.description,
            mime_type=mcp_resource.mimeType or "text/plain",
            icons=mcp_resource.icons,
            meta=mcp_resource.meta,
            tags=get_fastmcp_metadata(mcp_resource.meta).get("tags", []),
            task_config=TaskConfig(mode="forbidden"),
        )

    async def read(self) -> ResourceResult:
        """Read the resource content from the remote server."""
        if self._cached_content is not None:
            return self._cached_content

        backend_uri = self._backend_uri or str(self.uri)
        with client_span(
            f"resources/read {backend_uri}",
            "resources/read",
            backend_uri,
            resource_uri=backend_uri,
        ) as span:
            span.set_attribute("fastmcp.provider.type", "ProxyProvider")
            client = await self._get_client()
            async with client:
                result = await client.read_resource(backend_uri)
            if not result:
                raise ResourceError(
                    f"Remote server returned empty content for {backend_uri}"
                )

            # Process all items in the result list, not just the first one
            contents: list[ResourceContent] = []
            for item in result:
                if isinstance(item, TextResourceContents):
                    contents.append(
                        ResourceContent(
                            content=item.text,
                            mime_type=item.mimeType,
                            meta=item.meta,
                        )
                    )
                elif isinstance(item, BlobResourceContents):
                    contents.append(
                        ResourceContent(
                            content=base64.b64decode(item.blob),
                            mime_type=item.mimeType,
                            meta=item.meta,
                        )
                    )
                else:
                    raise ResourceError(f"Unsupported content type: {type(item)}")

            return ResourceResult(contents=contents)

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "ProxyProvider",
            "fastmcp.proxy.backend_uri": self._backend_uri,
        }


class ProxyTemplate(ResourceTemplate):
    """A ResourceTemplate that represents and creates resources from a remote server template."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")
    _backend_uri_template: str | None = None

    def __init__(self, client_factory: ClientFactoryT, **kwargs: Any):
        super().__init__(**kwargs)
        self._client_factory = client_factory

    async def _get_client(self) -> Client:
        """Gets a client instance by calling the sync or async factory."""
        client = self._client_factory()
        if inspect.isawaitable(client):
            client = await client
        return client

    def model_copy(self, **kwargs: Any) -> ProxyTemplate:
        """Override to preserve _backend_uri_template when uri_template changes."""
        update = kwargs.get("update", {})
        if "uri_template" in update and self._backend_uri_template is None:
            # First time uri_template is being changed, preserve original for backend
            update = {**update, "_backend_uri_template": self.uri_template}
            kwargs["update"] = update
        return super().model_copy(**kwargs)

    @classmethod
    def from_mcp_template(  # type: ignore[override]
        cls, client_factory: ClientFactoryT, mcp_template: mcp.types.ResourceTemplate
    ) -> ProxyTemplate:
        """Factory method to create a ProxyTemplate from a raw MCP template schema."""

        return cls(
            client_factory=client_factory,
            uri_template=mcp_template.uriTemplate,
            name=mcp_template.name,
            title=mcp_template.title,
            description=mcp_template.description,
            mime_type=mcp_template.mimeType or "text/plain",
            icons=mcp_template.icons,
            parameters={},  # Remote templates don't have local parameters
            meta=mcp_template.meta,
            tags=get_fastmcp_metadata(mcp_template.meta).get("tags", []),
            task_config=TaskConfig(mode="forbidden"),
        )

    async def create_resource(
        self,
        uri: str,
        params: dict[str, Any],
        context: Context | None = None,
    ) -> ProxyResource:
        """Create a resource from the template by calling the remote server."""
        # don't use the provided uri, because it may not be the same as the
        # uri_template on the remote server.
        # quote params to ensure they are valid for the uri_template
        backend_template = self._backend_uri_template or self.uri_template
        parameterized_uri = backend_template.format(
            **{k: quote(v, safe="") for k, v in params.items()}
        )
        client = await self._get_client()
        async with client:
            result = await client.read_resource(parameterized_uri)

        if not result:
            raise ResourceError(
                f"Remote server returned empty content for {parameterized_uri}"
            )

        # Process all items in the result list, not just the first one
        contents: list[ResourceContent] = []
        for item in result:
            if isinstance(item, TextResourceContents):
                contents.append(
                    ResourceContent(
                        content=item.text,
                        mime_type=item.mimeType,
                        meta=item.meta,
                    )
                )
            elif isinstance(item, BlobResourceContents):
                contents.append(
                    ResourceContent(
                        content=base64.b64decode(item.blob),
                        mime_type=item.mimeType,
                        meta=item.meta,
                    )
                )
            else:
                raise ResourceError(f"Unsupported content type: {type(item)}")

        cached_content = ResourceResult(contents=contents)

        return ProxyResource(
            client_factory=self._client_factory,
            uri=parameterized_uri,
            name=self.name,
            title=self.title,
            description=self.description,
            mime_type=result[
                0
            ].mimeType,  # Use first item's mimeType for backward compatibility
            icons=self.icons,
            meta=self.meta,
            tags=get_fastmcp_metadata(self.meta).get("tags", []),
            _cached_content=cached_content,
        )

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "ProxyProvider",
            "fastmcp.proxy.backend_uri_template": self._backend_uri_template,
        }


class ProxyPrompt(Prompt):
    """A Prompt that represents and renders a prompt from a remote server."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")
    _backend_name: str | None = None

    def __init__(self, client_factory: ClientFactoryT, **kwargs):
        super().__init__(**kwargs)
        self._client_factory = client_factory

    async def _get_client(self) -> Client:
        """Gets a client instance by calling the sync or async factory."""
        client = self._client_factory()
        if inspect.isawaitable(client):
            client = await client
        return client

    def model_copy(self, **kwargs: Any) -> ProxyPrompt:
        """Override to preserve _backend_name when name changes."""
        update = kwargs.get("update", {})
        if "name" in update and self._backend_name is None:
            # First time name is being changed, preserve original for backend calls
            update = {**update, "_backend_name": self.name}
            kwargs["update"] = update
        return super().model_copy(**kwargs)

    @classmethod
    def from_mcp_prompt(
        cls, client_factory: ClientFactoryT, mcp_prompt: mcp.types.Prompt
    ) -> ProxyPrompt:
        """Factory method to create a ProxyPrompt from a raw MCP prompt schema."""
        arguments = [
            PromptArgument(
                name=arg.name,
                description=arg.description,
                required=arg.required or False,
            )
            for arg in mcp_prompt.arguments or []
        ]
        return cls(
            client_factory=client_factory,
            name=mcp_prompt.name,
            title=mcp_prompt.title,
            description=mcp_prompt.description,
            arguments=arguments,
            icons=mcp_prompt.icons,
            meta=mcp_prompt.meta,
            tags=get_fastmcp_metadata(mcp_prompt.meta).get("tags", []),
            task_config=TaskConfig(mode="forbidden"),
        )

    async def render(self, arguments: dict[str, Any]) -> PromptResult:  # type: ignore[override]
        """Render the prompt by making a call through the client."""
        backend_name = self._backend_name or self.name
        with client_span(
            f"prompts/get {backend_name}", "prompts/get", backend_name
        ) as span:
            span.set_attribute("fastmcp.provider.type", "ProxyProvider")
            client = await self._get_client()
            async with client:
                result = await client.get_prompt(backend_name, arguments)
            # Convert GetPromptResult to PromptResult, preserving meta from result
            # (not the static prompt meta which includes fastmcp tags)
            # Convert PromptMessages to Messages
            messages = [
                Message(content=m.content, role=m.role) for m in result.messages
            ]
            return PromptResult(
                messages=messages,
                description=result.description,
                meta=result.meta,
            )

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "ProxyProvider",
            "fastmcp.proxy.backend_name": self._backend_name,
        }


# -----------------------------------------------------------------------------
# ProxyProvider
# -----------------------------------------------------------------------------


class ProxyProvider(Provider):
    """Provider that proxies to a remote MCP server via a client factory.

    This provider fetches components from a remote server and returns Proxy*
    component instances that forward execution to the remote server.

    All components returned by this provider have task_config.mode="forbidden"
    because tasks cannot be executed through a proxy.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.providers.proxy import ProxyProvider, ProxyClient

        # Create a proxy provider for a remote server
        proxy = ProxyProvider(lambda: ProxyClient("http://localhost:8000/mcp"))

        mcp = FastMCP("Proxy Server")
        mcp.add_provider(proxy)

        # Can also add with namespace
        mcp.add_provider(proxy.with_namespace("remote"))
        ```
    """

    def __init__(
        self,
        client_factory: ClientFactoryT,
    ):
        """Initialize a ProxyProvider.

        Args:
            client_factory: A callable that returns a Client instance when called.
                           This gives you full control over session creation and reuse.
                           Can be either a synchronous or asynchronous function.
        """
        super().__init__()
        self.client_factory = client_factory

    async def _get_client(self) -> Client:
        """Gets a client instance by calling the sync or async factory."""
        client = self.client_factory()
        if inspect.isawaitable(client):
            client = await client
        return client

    # -------------------------------------------------------------------------
    # Tool methods
    # -------------------------------------------------------------------------

    async def _list_tools(self) -> Sequence[Tool]:
        """List all tools from the remote server."""
        try:
            client = await self._get_client()
            async with client:
                mcp_tools = await client.list_tools()
                return [
                    ProxyTool.from_mcp_tool(self.client_factory, t) for t in mcp_tools
                ]
        except McpError as e:
            if e.error.code == METHOD_NOT_FOUND:
                return []
            raise

    # -------------------------------------------------------------------------
    # Resource methods
    # -------------------------------------------------------------------------

    async def _list_resources(self) -> Sequence[Resource]:
        """List all resources from the remote server."""
        try:
            client = await self._get_client()
            async with client:
                mcp_resources = await client.list_resources()
                return [
                    ProxyResource.from_mcp_resource(self.client_factory, r)
                    for r in mcp_resources
                ]
        except McpError as e:
            if e.error.code == METHOD_NOT_FOUND:
                return []
            raise

    # -------------------------------------------------------------------------
    # Resource template methods
    # -------------------------------------------------------------------------

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List all resource templates from the remote server."""
        try:
            client = await self._get_client()
            async with client:
                mcp_templates = await client.list_resource_templates()
                return [
                    ProxyTemplate.from_mcp_template(self.client_factory, t)
                    for t in mcp_templates
                ]
        except McpError as e:
            if e.error.code == METHOD_NOT_FOUND:
                return []
            raise

    # -------------------------------------------------------------------------
    # Prompt methods
    # -------------------------------------------------------------------------

    async def _list_prompts(self) -> Sequence[Prompt]:
        """List all prompts from the remote server."""
        try:
            client = await self._get_client()
            async with client:
                mcp_prompts = await client.list_prompts()
                return [
                    ProxyPrompt.from_mcp_prompt(self.client_factory, p)
                    for p in mcp_prompts
                ]
        except McpError as e:
            if e.error.code == METHOD_NOT_FOUND:
                return []
            raise

    # -------------------------------------------------------------------------
    # Task methods
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Return empty list since proxy components don't support tasks.

        Override the base implementation to avoid calling list_tools() during
        server lifespan initialization, which would open the client before any
        context is set. All Proxy* components have task_config.mode="forbidden".
        """
        return []

    # lifespan() uses default implementation (empty context manager)
    # because client cleanup is handled per-request


# -----------------------------------------------------------------------------
# Factory Functions
# -----------------------------------------------------------------------------


def _create_client_factory(
    target: (
        Client[ClientTransportT]
        | ClientTransport
        | FastMCP[Any]
        | FastMCP1Server
        | AnyUrl
        | Path
        | MCPConfig
        | dict[str, Any]
        | str
    ),
) -> ClientFactoryT:
    """Create a client factory from the given target.

    Internal helper that handles the session strategy based on the target type:
    - Connected Client: reuses existing session (with warning about context mixing)
    - Disconnected Client: creates fresh sessions per request
    - Other targets: creates ProxyClient and fresh sessions per request
    """
    if isinstance(target, Client):
        client = target
        if client.is_connected():
            logger.info(
                "Proxy detected connected client - reusing existing session for all requests. "
                "This may cause context mixing in concurrent scenarios."
            )

            def reuse_client_factory() -> Client:
                return client

            return reuse_client_factory
        else:

            def fresh_client_factory() -> Client:
                return client.new()

            return fresh_client_factory
    else:
        # target is not a Client, so it's compatible with ProxyClient.__init__
        base_client = ProxyClient(cast(Any, target))

        def proxy_client_factory() -> Client:
            return base_client.new()

        return proxy_client_factory


# -----------------------------------------------------------------------------
# FastMCPProxy - Convenience Wrapper
# -----------------------------------------------------------------------------


class FastMCPProxy(FastMCP):
    """A FastMCP server that acts as a proxy to a remote MCP-compliant server.

    This is a convenience wrapper that creates a FastMCP server with a
    ProxyProvider. For more control, use FastMCP with add_provider(ProxyProvider(...)).

    Example:
        ```python
        from fastmcp.server import create_proxy
        from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

        # Create a proxy server using create_proxy (recommended)
        proxy = create_proxy("http://localhost:8000/mcp")

        # Or use FastMCPProxy directly with explicit client factory
        proxy = FastMCPProxy(client_factory=lambda: ProxyClient("http://localhost:8000/mcp"))
        ```
    """

    def __init__(
        self,
        *,
        client_factory: ClientFactoryT,
        **kwargs,
    ):
        """Initialize the proxy server.

        FastMCPProxy requires explicit session management via client_factory.
        Use create_proxy() for convenience with automatic session strategy.

        Args:
            client_factory: A callable that returns a Client instance when called.
                           This gives you full control over session creation and reuse.
                           Can be either a synchronous or asynchronous function.
            **kwargs: Additional settings for the FastMCP server.
        """
        super().__init__(**kwargs)
        self.client_factory = client_factory
        provider: Provider = ProxyProvider(client_factory)
        self.add_provider(provider)


# -----------------------------------------------------------------------------
# ProxyClient and Related
# -----------------------------------------------------------------------------


async def default_proxy_roots_handler(
    context: RequestContext[ClientSession, LifespanContextT],
) -> RootsList:
    """Forward list roots request from remote server to proxy's connected clients."""
    ctx = get_context()
    return await ctx.list_roots()


async def default_proxy_sampling_handler(
    messages: list[mcp.types.SamplingMessage],
    params: mcp.types.CreateMessageRequestParams,
    context: RequestContext[ClientSession, LifespanContextT],
) -> mcp.types.CreateMessageResult:
    """Forward sampling request from remote server to proxy's connected clients."""
    ctx = get_context()
    result = await ctx.sample(
        list(messages),
        system_prompt=params.systemPrompt,
        temperature=params.temperature,
        max_tokens=params.maxTokens,
        model_preferences=params.modelPreferences,
    )
    content = mcp.types.TextContent(type="text", text=result.text or "")
    return mcp.types.CreateMessageResult(
        role="assistant",
        model="fastmcp-client",
        # TODO(ty): remove when ty supports isinstance exclusion narrowing
        content=content,
    )


async def default_proxy_elicitation_handler(
    message: str,
    response_type: type,
    params: mcp.types.ElicitRequestParams,
    context: RequestContext[ClientSession, LifespanContextT],
) -> ElicitResult:
    """Forward elicitation request from remote server to proxy's connected clients."""
    ctx = get_context()
    # requestedSchema only exists on ElicitRequestFormParams, not ElicitRequestURLParams
    requested_schema = (
        params.requestedSchema
        if isinstance(params, ElicitRequestFormParams)
        else {"type": "object", "properties": {}}
    )
    result = await ctx.session.elicit(
        message=message,
        requestedSchema=requested_schema,
        related_request_id=ctx.request_id,
    )
    return ElicitResult(action=result.action, content=result.content)


async def default_proxy_log_handler(message: LogMessage) -> None:
    """Forward log notification from remote server to proxy's connected clients."""
    ctx = get_context()
    msg = message.data.get("msg")
    extra = message.data.get("extra")
    await ctx.log(msg, level=message.level, logger_name=message.logger, extra=extra)


async def default_proxy_progress_handler(
    progress: float,
    total: float | None,
    message: str | None,
) -> None:
    """Forward progress notification from remote server to proxy's connected clients."""
    ctx = get_context()
    await ctx.report_progress(progress, total, message)


class ProxyClient(Client[ClientTransportT]):
    """A proxy client that forwards advanced interactions between a remote MCP server and the proxy's connected clients.

    Supports forwarding roots, sampling, elicitation, logging, and progress.
    """

    # Stored context for handlers when contextvar isn't available
    # (e.g., when receive loop was started before any request context)
    _proxy_context: Context | None = None

    def __init__(
        self,
        transport: ClientTransportT
        | FastMCP[Any]
        | FastMCP1Server
        | AnyUrl
        | Path
        | MCPConfig
        | dict[str, Any]
        | str,
        **kwargs,
    ):
        if "name" not in kwargs:
            kwargs["name"] = self.generate_name()
        if "roots" not in kwargs:
            kwargs["roots"] = default_proxy_roots_handler
        if "sampling_handler" not in kwargs:
            kwargs["sampling_handler"] = default_proxy_sampling_handler
        if "elicitation_handler" not in kwargs:
            kwargs["elicitation_handler"] = default_proxy_elicitation_handler
        if "log_handler" not in kwargs:
            kwargs["log_handler"] = default_proxy_log_handler
        if "progress_handler" not in kwargs:
            kwargs["progress_handler"] = default_proxy_progress_handler
        super().__init__(**kwargs | {"transport": transport})


class StatefulProxyClient(ProxyClient[ClientTransportT]):
    """A proxy client that provides a stateful client factory for the proxy server.

    The stateful proxy client bound its copy to the server session.
    And it will be disconnected when the session is exited.

    This is useful to proxy a stateful mcp server such as the Playwright MCP server.
    Note that it is essential to ensure that the proxy server itself is also stateful.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._caches: dict[ServerSession, Client[ClientTransportT]] = {}

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[override]
        """The stateful proxy client will be forced disconnected when the session is exited.

        So we do nothing here.
        """

    async def clear(self):
        """Clear all cached clients and force disconnect them."""
        while self._caches:
            _, cache = self._caches.popitem()
            await cache._disconnect(force=True)

    def new_stateful(self) -> Client[ClientTransportT]:
        """Create a new stateful proxy client instance with the same configuration.

        Use this method as the client factory for stateful proxy server.
        """
        session = get_context().session
        proxy_client = self._caches.get(session, None)

        if proxy_client is None:
            proxy_client = self.new()
            logger.debug(f"{proxy_client} created for {session}")
            self._caches[session] = proxy_client

            async def _on_session_exit():
                self._caches.pop(session)
                logger.debug(f"{proxy_client} will be disconnect")
                await proxy_client._disconnect(force=True)

            session._exit_stack.push_async_callback(_on_session_exit)

        return proxy_client
