"""ProxyProvider for proxying to remote MCP servers.

This module provides the `ProxyProvider` class that proxies components from
a remote MCP server via a client factory. It also provides proxy component
classes that forward execution to remote servers.
"""

from __future__ import annotations

import base64
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

import anyio
import httpx2
import mcp_types
from mcp import ClientSession
from mcp.server.connection import Connection
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import (
    METHOD_NOT_FOUND,
    BlobResourceContents,
    ElicitRequestFormParams,
    TextResourceContents,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic.networks import AnyUrl

from fastmcp.client.client import Client, SDKServer
from fastmcp.client.elicitation import ElicitResult, create_elicitation_callback
from fastmcp.client.logging import LogMessage, create_log_callback
from fastmcp.client.roots import RootsList, create_roots_callback
from fastmcp.client.sampling import create_sampling_callback
from fastmcp.client.telemetry import client_span
from fastmcp.client.transports import ClientTransportT
from fastmcp.client.transports.base import TransportOptions
from fastmcp.exceptions import ResourceError
from fastmcp.mcp_config import MCPConfig
from fastmcp.prompts import Message, Prompt, PromptResult
from fastmcp.prompts.base import PromptArgument
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.resources.base import ResourceContent, ResourceResult
from fastmcp.resources.template import expand_uri_template
from fastmcp.server.context import Context
from fastmcp.server.dependencies import fastmcp_request_ctx, get_context
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.aggregate import ProviderErrorStrategy
from fastmcp.server.providers.base import Provider
from fastmcp.server.server import FastMCP
from fastmcp.telemetry import inject_trace_context
from fastmcp.tools.base import InputRequiredToolResult, Tool, ToolResult
from fastmcp.utilities.components import FastMCPComponent, get_fastmcp_metadata
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.tasks import TaskConfig
from fastmcp.utilities.versions import VersionSpec, version_sort_key

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.client.transports import ClientTransport

logger = get_logger(__name__)

# Type alias for client factory functions
ClientFactoryT = Callable[[], Client] | Callable[[], Awaitable[Client]]


class _ForwardingClientSession(ClientSession):
    """A session that does not enforce the backend's declared output schema.

    `ClientSession.call_tool` normally validates a tool's structured content
    against the output schema the backend advertised, raising if they disagree.
    That check belongs to whoever consumes the result. A proxy only relays it,
    and the end client runs the same check for itself, so enforcing it mid-path
    turns a backend's schema bug into a proxy error and hides the real response.
    """

    async def validate_tool_result(
        self, name: str, result: mcp_types.CallToolResult
    ) -> None:
        return None


# Settings every proxy-backend connection uses: relay results without policing
# the backend's output schema, and forward the caller's authorization header
# upstream (appropriate for a proxy, where credentials are meant to propagate).
PROXY_TRANSPORT_OPTIONS = TransportOptions(
    session_class=_ForwardingClientSession,
    forward_incoming_headers=True,
)


#: Transport-level failures that can escape a backend connection attempt.
#: `Client._connect` wraps most connect failures in a ``RuntimeError("Client
#: failed to connect: ...")``, but a transport can also surface an httpx or
#: anyio stream error directly. Every proxy entry point that opens a backend
#: connection normalizes these into an ``MCPError`` so callers see a protocol
#: error instead of a raw transport exception.
_PROXY_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    RuntimeError,
    TimeoutError,
    httpx2.HTTPError,
    anyio.ClosedResourceError,
    anyio.EndOfStream,
    anyio.BrokenResourceError,
)


def _proxy_upstream_error(error: Exception) -> MCPError:
    return MCPError(
        code=mcp_types.INTERNAL_ERROR,
        message=str(error),
    )


def _stash_proxy_request_context(client: Client, ctx: Context) -> None:
    """Stash the proxy's ``RequestContext`` on a ``ProxyClient`` before a backend call.

    Every proxy component (tool, resource, template, prompt) must call this
    before relaying to its backend so the forwarding handlers can restore the
    proxy's request context before relaying a server-initiated request
    (roots/sampling/elicitation) back to the proxy's client. Required for every
    proxy client: under SDK v2 an in-memory backend shares this event loop, so a
    handler's ``get_context()`` would otherwise resolve to the backend context
    and the server-initiated request would hang until timeout.

    We stash a ``(RequestContext, weakref[FastMCP])`` tuple — never a ``Context``
    instance — because ``Context`` properties are themselves ContextVar-dependent
    and would resolve stale values in the receive loop.
    """
    if isinstance(client, ProxyClient):
        client._proxy_rc_ref[0] = (
            ctx.request_context,
            ctx._fastmcp,  # weakref to FastMCP, not the Context
        )


class ProxyInitializeMiddleware(Middleware):
    def __init__(self, proxy: FastMCPProxy) -> None:
        self.proxy = proxy

    async def on_initialize(
        self,
        context: MiddlewareContext[mcp_types.InitializeRequest],
        call_next: CallNext[
            mcp_types.InitializeRequest,
            mcp_types.InitializeResult | None,
        ],
    ) -> mcp_types.InitializeResult | None:
        client = await self.proxy._get_client()
        upstream_instructions: str | None = None
        try:
            if isinstance(client, ProxyClient):
                ctx = context.fastmcp_context
                if ctx is not None:
                    client._proxy_rc_ref[0] = (
                        ctx.request_context,
                        ctx._fastmcp,
                    )
            async with client:
                # Entering the context already ran connect-time negotiation.
                # `initialize()` returns the handshake result on a legacy backend,
                # but raises on a modern (server/discover) backend, which has no
                # InitializeResult. That mismatch only arises when an explicit
                # `mode=` pins the backend to a different era than this legacy
                # front (the era-mirroring default keeps the two in lockstep, so
                # a legacy front always reaches a legacy backend here). Skip the
                # handshake-only call when the backend negotiated the modern era.
                if client.protocol_version not in MODERN_PROTOCOL_VERSIONS:
                    await client.initialize()
                    # Capture the upstream's instructions while the session is
                    # live; `initialize_result` clears once the context exits.
                    init_result = client.initialize_result
                    if init_result is not None:
                        upstream_instructions = init_result.instructions
        except MCPError:
            raise
        except _PROXY_TRANSPORT_ERRORS as error:
            raise _proxy_upstream_error(error) from error

        result = await call_next(context)

        # Forward the upstream server's instructions unless the proxy defines its
        # own. `instructions` is part of the MCP InitializeResult and is meant to
        # steer the model, so a proxy that dropped it would silently degrade any
        # downstream consumer relying on upstream guidance.
        if (
            result is not None
            and self.proxy.instructions is None
            and upstream_instructions is not None
        ):
            result.instructions = upstream_instructions

        return result


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
            client = cast(Client, await client)
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
        cls, client_factory: ClientFactoryT, mcp_tool: mcp_types.Tool
    ) -> ProxyTool:
        """Factory method to create a ProxyTool from a raw MCP tool schema."""
        return cls(
            client_factory=client_factory,
            name=mcp_tool.name,
            title=mcp_tool.title,
            description=mcp_tool.description,
            parameters=mcp_tool.input_schema,
            annotations=mcp_tool.annotations,
            output_schema=mcp_tool.output_schema,
            icons=mcp_tool.icons,
            meta=mcp_tool.meta,
            tags=get_fastmcp_metadata(mcp_tool.meta).get("tags", []),
            execution=mcp_tool.execution,
        )

    async def run(
        self,
        arguments: dict[str, Any],
        context: Context | None = None,
    ) -> ToolResult:
        """Executes the tool by making a call through the client."""
        backend_name = self._backend_name or self.name
        with client_span(
            f"tools/call {backend_name}",
            "tools/call",
            backend_name,
            tool_name=backend_name,
        ) as span:
            span.set_attribute("fastmcp.provider.type", "ProxyProvider")
            client = await self._get_client()
            async with client:
                ctx = context or get_context()
                _stash_proxy_request_context(client, ctx)
                # Forward the inbound request's `_meta` block (trace context,
                # version, etc.) to the backend. In SDK v2 the request context
                # exposes the lifted `_meta` dict directly; task submission is a
                # first-class params field rather than context state, so there
                # is no separate task-metadata injection here.
                req_ctx = ctx.request_context
                meta: dict[str, Any] | None = (
                    dict(req_ctx.meta) if req_ctx is not None and req_ctx.meta else None
                )

                if client.protocol_version in MODERN_PROTOCOL_VERSIONS:
                    # Modern backend: call the session directly (not
                    # `call_tool_mcp`, which would *drive* a multi-round-trip ask
                    # to completion on this proxy). A guard tool's
                    # `InputRequiredResult` (SEP-2322) must instead surface as a
                    # result so the parent's middleware and wire seam own the
                    # round. Forward the inbound request's continuation state
                    # down so the backend guard tool sees the client's answers
                    # on its own `ctx.input_responses` / `ctx.request_state`.
                    request_meta = cast(
                        "mcp_types.RequestParamsMeta | None",
                        inject_trace_context(meta) or None,
                    )
                    result = await client._await_with_session_monitoring(
                        client.session.call_tool(
                            name=backend_name,
                            arguments=arguments,
                            meta=request_meta,
                            # Forward upstream progress the same way the legacy
                            # `call_tool_mcp` path does — without this handler a
                            # backend tool's `ctx.report_progress()` is dropped
                            # on modern proxy calls.
                            progress_callback=client._progress_handler,
                            input_responses=ctx.input_responses,
                            request_state=ctx.request_state,
                            allow_input_required=True,
                        )
                    )
                    # A backend ask round-trips into an InputRequiredToolResult
                    # so the parent's middleware observes it and the parent's
                    # wire handler unwraps it (era-gated on the parent's own
                    # connection).
                    if isinstance(result, mcp_types.InputRequiredResult):
                        return InputRequiredToolResult(result)
                    tool_result = cast("mcp_types.CallToolResult", result)
                else:
                    # Legacy backend: the multi-round-trip result type does not
                    # exist there, so keep the original path.
                    tool_result = await client.call_tool_mcp(
                        name=backend_name, arguments=arguments, meta=meta
                    )
            # Pass an upstream error result through faithfully rather than
            # collapsing it into a raised ToolError — this preserves the
            # backend's content (including non-text and structured content),
            # and the client still raises on isError by default.
            # Preserve backend's meta (includes task metadata for background tasks)
            return ToolResult(
                content=tool_result.content,
                structured_content=tool_result.structured_content,
                meta=tool_result.meta,
                is_error=tool_result.is_error,
            )

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "ProxyProvider",
            "fastmcp.proxy.backend_name": self._backend_name or self.name,
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
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._client_factory = client_factory
        self._cached_content = _cached_content

    async def _get_client(self) -> Client:
        """Gets a client instance by calling the sync or async factory."""
        client = self._client_factory()
        if inspect.isawaitable(client):
            client = cast(Client, await client)
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
        mcp_resource: mcp_types.Resource,
    ) -> ProxyResource:
        """Factory method to create a ProxyResource from a raw MCP resource schema."""

        return cls(
            client_factory=client_factory,
            uri=mcp_resource.uri,
            name=mcp_resource.name,
            title=mcp_resource.title,
            description=mcp_resource.description,
            mime_type=mcp_resource.mime_type or "text/plain",
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
            "resources/read",
            "resources/read",
            backend_uri,
            resource_uri=backend_uri,
        ) as span:
            span.set_attribute("fastmcp.provider.type", "ProxyProvider")
            client = await self._get_client()
            async with client:
                _stash_proxy_request_context(client, get_context())
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
                            mime_type=item.mime_type,
                            meta=item.meta,
                        )
                    )
                elif isinstance(item, BlobResourceContents):
                    contents.append(
                        ResourceContent(
                            content=base64.b64decode(item.blob),
                            mime_type=item.mime_type,
                            meta=item.meta,
                        )
                    )
                else:
                    raise ResourceError(f"Unsupported content type: {type(item)}")

            return ResourceResult(contents=contents)

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "ProxyProvider",
            "fastmcp.proxy.backend_uri": self._backend_uri or str(self.uri),
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
            client = cast(Client, await client)
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
        cls, client_factory: ClientFactoryT, mcp_template: mcp_types.ResourceTemplate
    ) -> ProxyTemplate:  # ty:ignore[invalid-method-override]
        """Factory method to create a ProxyTemplate from a raw MCP template schema."""

        return cls(
            client_factory=client_factory,
            uri_template=mcp_template.uri_template,
            name=mcp_template.name,
            title=mcp_template.title,
            description=mcp_template.description,
            mime_type=mcp_template.mime_type or "text/plain",
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
        # uri_template on the remote server. expand_uri_template percent-encodes
        # path and query values so the backend URI round-trips correctly.
        backend_template = self._backend_uri_template or self.uri_template
        parameterized_uri = expand_uri_template(backend_template, params)
        client = await self._get_client()
        async with client:
            _stash_proxy_request_context(client, context or get_context())
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
                        mime_type=item.mime_type,
                        meta=item.meta,
                    )
                )
            elif isinstance(item, BlobResourceContents):
                contents.append(
                    ResourceContent(
                        content=base64.b64decode(item.blob),
                        mime_type=item.mime_type,
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
            ].mime_type,  # Use first item's mimeType for backward compatibility
            icons=self.icons,
            meta=self.meta,
            tags=get_fastmcp_metadata(self.meta).get("tags", []),
            _cached_content=cached_content,
        )

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.provider.type": "ProxyProvider",
            "fastmcp.proxy.backend_uri_template": (
                self._backend_uri_template or self.uri_template
            ),
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
            client = cast(Client, await client)
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
        cls, client_factory: ClientFactoryT, mcp_prompt: mcp_types.Prompt
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

    async def render(self, arguments: dict[str, Any]) -> PromptResult:  # type: ignore[override]  # ty:ignore[invalid-method-override]
        """Render the prompt by making a call through the client."""
        backend_name = self._backend_name or self.name
        with client_span(
            f"prompts/get {backend_name}",
            "prompts/get",
            backend_name,
            prompt_name=backend_name,
        ) as span:
            span.set_attribute("fastmcp.provider.type", "ProxyProvider")
            client = await self._get_client()
            async with client:
                _stash_proxy_request_context(client, get_context())
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
            "fastmcp.proxy.backend_name": self._backend_name or self.name,
        }


# -----------------------------------------------------------------------------
# ProxyProvider
# -----------------------------------------------------------------------------


class _CacheEntry:
    """A cached sequence of components with a monotonic timestamp."""

    __slots__ = ("items", "timestamp")

    def __init__(self, items: Sequence[Any], timestamp: float):
        self.items = items
        self.timestamp = timestamp

    def is_fresh(self, ttl: float) -> bool:
        return (time.monotonic() - self.timestamp) < ttl


_DEFAULT_CACHE_TTL: float = 300.0


class ProxyProvider(Provider):
    """Provider that proxies to a remote MCP server via a client factory.

    This provider fetches components from a remote server and returns Proxy*
    component instances that forward execution to the remote server.

    All components returned by this provider have task_config.mode="forbidden"
    because tasks cannot be executed through a proxy.

    Component lists (tools, resources, templates, prompts) are cached so that
    individual lookups (e.g. during ``call_tool``) can resolve from the cache
    instead of opening a new backend connection.  The cache stores the
    backend's raw component metadata and is shared across all sessions;
    per-session visibility and auth filtering are applied after cache lookup
    by the server layer.  The cache is refreshed whenever a ``list_*`` call
    is made, and entries expire after ``cache_ttl`` seconds (default 300).
    Set ``cache_ttl=0`` to disable caching.  Disabling is recommended for
    backends whose component lists change dynamically.

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
        cache_ttl: float | None = None,
    ):
        """Initialize a ProxyProvider.

        Args:
            client_factory: A callable that returns a Client instance when called.
                           This gives you full control over session creation and reuse.
                           Can be either a synchronous or asynchronous function.
            cache_ttl: How long (in seconds) to cache component lists for
                      individual lookups.  Defaults to 300.  Set to 0 to
                      disable caching.
        """
        super().__init__()
        self.client_factory = client_factory
        self._cache_ttl = cache_ttl if cache_ttl is not None else _DEFAULT_CACHE_TTL
        self._tools_cache: _CacheEntry[Tool] | None = None
        self._resources_cache: _CacheEntry[Resource] | None = None
        self._templates_cache: _CacheEntry[ResourceTemplate] | None = None
        self._prompts_cache: _CacheEntry[Prompt] | None = None

    async def _get_client(self) -> Client:
        """Gets a client instance by calling the sync or async factory."""
        client = self.client_factory()
        if inspect.isawaitable(client):
            client = cast(Client, await client)
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
                tools = [
                    ProxyTool.from_mcp_tool(self.client_factory, t) for t in mcp_tools
                ]
        except MCPError as e:
            if e.error.code == METHOD_NOT_FOUND:
                tools = []
            else:
                raise
        except _PROXY_TRANSPORT_ERRORS as error:
            raise _proxy_upstream_error(error) from error
        self._tools_cache = _CacheEntry(tools, time.monotonic())
        return tools

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        cache = self._tools_cache
        if cache is None or not cache.is_fresh(self._cache_ttl):
            await self._list_tools()
            cache = self._tools_cache
        assert cache is not None
        matching = [t for t in cache.items if t.name == name]
        if version:
            matching = [t for t in matching if version.matches(t.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)

    # -------------------------------------------------------------------------
    # Resource methods
    # -------------------------------------------------------------------------

    async def _list_resources(self) -> Sequence[Resource]:
        """List all resources from the remote server."""
        try:
            client = await self._get_client()
            async with client:
                mcp_resources = await client.list_resources()
                resources = [
                    ProxyResource.from_mcp_resource(self.client_factory, r)
                    for r in mcp_resources
                ]
        except MCPError as e:
            if e.error.code == METHOD_NOT_FOUND:
                resources = []
            else:
                raise
        except _PROXY_TRANSPORT_ERRORS as error:
            raise _proxy_upstream_error(error) from error
        self._resources_cache = _CacheEntry(resources, time.monotonic())
        return resources

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        cache = self._resources_cache
        if cache is None or not cache.is_fresh(self._cache_ttl):
            await self._list_resources()
            cache = self._resources_cache
        assert cache is not None
        matching = [r for r in cache.items if str(r.uri) == uri]
        if version:
            matching = [r for r in matching if version.matches(r.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)

    # -------------------------------------------------------------------------
    # Resource template methods
    # -------------------------------------------------------------------------

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List all resource templates from the remote server."""
        try:
            client = await self._get_client()
            async with client:
                mcp_templates = await client.list_resource_templates()
                templates = [
                    ProxyTemplate.from_mcp_template(self.client_factory, t)
                    for t in mcp_templates
                ]
        except MCPError as e:
            if e.error.code == METHOD_NOT_FOUND:
                templates = []
            else:
                raise
        except _PROXY_TRANSPORT_ERRORS as error:
            raise _proxy_upstream_error(error) from error
        self._templates_cache = _CacheEntry(templates, time.monotonic())
        return templates

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        cache = self._templates_cache
        if cache is None or not cache.is_fresh(self._cache_ttl):
            await self._list_resource_templates()
            cache = self._templates_cache
        assert cache is not None
        matching = [t for t in cache.items if t.matches(uri) is not None]
        if version:
            matching = [t for t in matching if version.matches(t.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)

    # -------------------------------------------------------------------------
    # Prompt methods
    # -------------------------------------------------------------------------

    async def _list_prompts(self) -> Sequence[Prompt]:
        """List all prompts from the remote server."""
        try:
            client = await self._get_client()
            async with client:
                mcp_prompts = await client.list_prompts()
                prompts = [
                    ProxyPrompt.from_mcp_prompt(self.client_factory, p)
                    for p in mcp_prompts
                ]
        except MCPError as e:
            if e.error.code == METHOD_NOT_FOUND:
                prompts = []
            else:
                raise
        except _PROXY_TRANSPORT_ERRORS as error:
            raise _proxy_upstream_error(error) from error
        self._prompts_cache = _CacheEntry(prompts, time.monotonic())
        return prompts

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        cache = self._prompts_cache
        if cache is None or not cache.is_fresh(self._cache_ttl):
            await self._list_prompts()
            cache = self._prompts_cache
        assert cache is not None
        matching = [p for p in cache.items if p.name == name]
        if version:
            matching = [p for p in matching if version.matches(p.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)

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


def _mirror_front_era_mode() -> str | None:
    """Return the backend connect ``mode`` that mirrors the front connection's era.

    A proxy is a server on its front and a client on its back. The two protocol
    eras have mutually exclusive interaction models on a single session, so the
    whole chain must speak one era end-to-end: a modern front must reach a modern
    backend (a guard tool's `InputRequiredResult` round-trips), and a handshake
    front must reach a handshake backend (server-initiated sampling / elicitation
    / roots push-forwarding works). Rather than pin its own era, the proxy speaks
    on its back whatever era was negotiated on its front.

    Reads the negotiated protocol version from the active front request context:

    - modern front → that exact version, so the backend negotiates the same era
      (pinning the version rather than ``"auto"`` makes the eras truly match).
    - handshake front → ``"legacy"``.
    - no request context (e.g. proxy construction before any request) → ``None``,
      leaving the factory's configured default mode in place.
    """
    try:
        ctx = get_context()
    except RuntimeError:
        return None
    rc = ctx.request_context
    if rc is None:
        return None
    version = rc.protocol_version
    if version in MODERN_PROTOCOL_VERSIONS:
        return version
    return "legacy"


def _create_client_factory(
    target: (
        Client[ClientTransportT]
        | ClientTransport
        | FastMCP[Any]
        | SDKServer
        | AnyUrl
        | Path
        | MCPConfig
        | dict[str, Any]
        | str
    ),
    *,
    mode: str | None = None,
) -> ClientFactoryT:
    """Create a client factory from the given target.

    Internal helper that handles the session strategy based on the target type:
    - Connected Client: reuses existing session (with warning about context mixing)
    - Disconnected Client: creates fresh sessions per request
    - Other targets: creates ProxyClient and fresh sessions per request
    """
    if isinstance(target, Client):
        client = target

        def as_proxy_backend(c: Client) -> Client:
            """Apply proxy connection settings to a copy we own.

            The caller handed us their Client; configuring it in place would
            change how their own connections behave, including whether their
            credentials get forwarded upstream.
            """
            fresh = c.new()
            # The caller chose this client's era, so a multi-server MCPConfig
            # target's mounted backend legs should negotiate it too rather than
            # stopping at the composite router (see
            # `TransportOptions.backend_mode`).
            fresh._transport_options = replace(
                PROXY_TRANSPORT_OPTIONS, backend_mode=fresh.mode
            )
            return fresh

        if client.is_connected() and type(client) is ProxyClient:
            logger.info(
                "Proxy detected connected ProxyClient - creating fresh sessions for each "
                "request to avoid request context leakage."
            )

            def fresh_client_factory() -> Client:
                return as_proxy_backend(client)

            return fresh_client_factory

        if client.is_connected():
            logger.info(
                "Proxy detected connected client - reusing existing session for all requests. "
                "This may cause context mixing in concurrent scenarios, and the session's "
                "existing settings apply, so backend results are validated against their "
                "declared output schema rather than relayed as-is. Pass a disconnected "
                "client to avoid both."
            )

            # The caller's session is already built, so there are no connection
            # settings left to apply — proxy options only take effect at connect
            # time. Reuse is opt-in via passing an already-connected client.
            def reuse_client_factory() -> Client:
                return client

            return reuse_client_factory

        def fresh_client_factory() -> Client:
            return as_proxy_backend(client)

        return fresh_client_factory
    else:
        # target is not a Client, so it's compatible with ProxyClient.__init__.
        #
        # With no explicit mode, the backend MIRRORS the front connection's
        # negotiated era per request (see `_mirror_front_era_mode`): a fresh
        # client is built for each request and its mode is set from the front
        # era, so the whole chain speaks one era end-to-end. Because every
        # request gets its own client whose mode is derived at call time, front
        # connections of different eras never share a backend session — there is
        # no era to bleed across the (metadata-only) provider caches.
        #
        # An explicit mode pins the backend era regardless of the front. This
        # breaks era-consistency and is only appropriate when the backend speaks
        # a single era; the mismatch surfaces through the normal era gates.
        explicit_mode = mode is not None
        client_kwargs: dict[str, Any] = {"mode": mode} if explicit_mode else {}
        base_client = ProxyClient(cast(Any, target), **client_kwargs)

        def proxy_client_factory() -> Client:
            fresh = base_client.new()
            backend_mode = mode
            if not explicit_mode:
                backend_mode = _mirror_front_era_mode()
                if backend_mode is not None:
                    fresh.mode = backend_mode
            if backend_mode is not None:
                # A multi-server MCPConfig target reaches its real backends
                # through proxies mounted on a composite router, so setting the
                # era on this client alone would stop at the router. Carry the
                # era down to those backend legs too (see
                # `TransportOptions.backend_mode`), resolved here — at the
                # moment a client is built for this request — so it tracks the
                # front era rather than whatever was true at construction.
                fresh._transport_options = replace(
                    PROXY_TRANSPORT_OPTIONS, backend_mode=backend_mode
                )
            return fresh

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
        provider_error_strategy: ProviderErrorStrategy = "warn",
        **kwargs,
    ):
        """Initialize the proxy server.

        FastMCPProxy requires explicit session management via client_factory.
        Use create_proxy() for convenience with automatic session strategy.

        Args:
            client_factory: A callable that returns a Client instance when called.
                           This gives you full control over session creation and reuse.
                           Can be either a synchronous or asynchronous function.
            provider_error_strategy: How provider errors should affect aggregate
                operations. Defaults to ``"warn"`` for compatibility; use
                ``"raise"`` when the proxy should surface upstream failures.
            **kwargs: Additional settings for the FastMCP server.
        """
        super().__init__(**kwargs)
        self.provider_error_strategy = provider_error_strategy
        self.client_factory = client_factory
        provider: Provider = ProxyProvider(client_factory)
        self.add_provider(provider)
        self.middleware.append(ProxyInitializeMiddleware(self))
        self._setup_proxy_ping_handler()
        self._setup_proxy_discover_handler()

    async def _get_client(self) -> Client:
        client = self.client_factory()
        if inspect.isawaitable(client):
            client = cast(Client, await client)
        return client

    def _setup_proxy_ping_handler(self) -> None:
        async def ping_remote(
            _ctx: ServerRequestContext[Any, Any],
            _params: mcp_types.RequestParams | None,
        ) -> mcp_types.EmptyResult:
            client = await self._get_client()
            async with client:
                await client.ping()
            return mcp_types.EmptyResult()

        self._mcp_server.add_request_handler(
            "ping", mcp_types.RequestParams, ping_remote
        )

    def _setup_proxy_discover_handler(self) -> None:
        """Forward the backend's instructions on the modern (`server/discover`) path.

        `ProxyInitializeMiddleware` forwards upstream instructions by patching
        the `InitializeResult`, but `on_initialize` only fires for the legacy
        handshake. A modern client negotiates via `server/discover`, whose
        default SDK handler reads `self.instructions` off the low-level server
        directly, so a proxy would silently drop its upstream's instructions for
        every modern client.

        The SDK sanctions replacing this handler wholesale, so we delegate to
        its own implementation for the rest of the result (supported versions,
        capabilities, server info) and only fill in the instructions we would
        otherwise lose. Resolving them here — at request time, from a live
        backend session — keeps the proxy's lazy-connect contract intact: the
        backend is contacted when a client actually asks, never at construction.
        """
        build_default_result = self._mcp_server._handle_discover

        async def discover_remote(
            ctx: ServerRequestContext[Any, Any],
            params: mcp_types.RequestParams | None,
        ) -> mcp_types.DiscoverResult:
            result = await build_default_result(ctx, params)
            # A proxy with its own instructions keeps them, matching the
            # precedence `ProxyInitializeMiddleware` applies on the legacy path.
            if result.instructions is not None:
                return result
            client = await self._get_client()
            # `session.instructions` is era-neutral: it reads the backend's
            # `DiscoverResult` or `InitializeResult` depending on what the
            # backend negotiated, so a modern front can proxy a legacy backend.
            if client.is_connected():
                result.instructions = client.session.instructions
                return result
            # Era mirroring pins a modern backend to an exact version, and a
            # pinned version adopts a synthesized `DiscoverResult` instead of
            # probing the wire — so the pinned client would report no
            # instructions at all. Instructions are metadata with no
            # back-channel, so this read does not need the era consistency
            # mirroring exists to protect; negotiate with "auto" instead, which
            # probes `server/discover` and falls back to the handshake for a
            # legacy-only backend.
            client.mode = "auto"
            try:
                async with client:
                    result.instructions = client.session.instructions
            except MCPError:
                raise
            except _PROXY_TRANSPORT_ERRORS as error:
                raise _proxy_upstream_error(error) from error
            return result

        self._mcp_server.add_request_handler(
            "server/discover", mcp_types.RequestParams, discover_remote
        )


# -----------------------------------------------------------------------------
# ProxyClient and Related
# -----------------------------------------------------------------------------


async def default_proxy_roots_handler(
    context: ServerRequestContext[Any, Any],
) -> RootsList:
    """Forward list roots request from remote server to proxy's connected clients."""
    ctx = get_context()
    return await ctx.list_roots()


async def default_proxy_sampling_handler(
    messages: list[mcp_types.SamplingMessage],
    params: mcp_types.CreateMessageRequestParams,
    context: ServerRequestContext[Any, Any],
) -> mcp_types.CreateMessageResult:
    """Forward sampling request from remote server to proxy's connected clients."""
    ctx = get_context()
    result = await ctx.sample(
        list(messages),
        system_prompt=params.system_prompt,
        temperature=params.temperature,
        max_tokens=params.max_tokens,
        model_preferences=params.model_preferences,
    )
    content = mcp_types.TextContent(type="text", text=result.text or "")
    return mcp_types.CreateMessageResult(
        role="assistant",
        model="fastmcp-client",
        # TODO(ty): remove when ty supports isinstance exclusion narrowing
        content=content,
    )


async def default_proxy_elicitation_handler(
    message: str,
    response_type: type,
    params: mcp_types.ElicitRequestParams,
    context: ServerRequestContext[Any, Any],
) -> ElicitResult:
    """Forward elicitation request from remote server to proxy's connected clients."""
    ctx = get_context()
    # requestedSchema only exists on ElicitRequestFormParams, not ElicitRequestURLParams
    requested_schema = (
        params.requested_schema
        if isinstance(params, ElicitRequestFormParams)
        else {"type": "object", "properties": {}}
    )
    result = await ctx.session.elicit(
        message=message,
        requested_schema=requested_schema,
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


def _restore_request_context(
    rc_ref: list[Any],
) -> None:
    """Set the ``request_ctx``, ``_current_context`` and ``_current_server``
    ContextVars from stashed values so a proxy forwarding handler relays to the
    proxy's own client rather than the upstream server.

    Called at the start of every proxy handler invocation. The stashed proxy
    ``RequestContext`` is the correct forwarding target, so we restore it unless
    it is already active. This covers two cases:

    - Stateful proxy: the reused receive-loop task carries a stale ContextVar
      from an earlier request (same session, different request_id).
    - In-memory backend (SDK v2): the backend runs in this event loop, so the
      handler may inherit the *backend's* request_ctx (a different session).

    We stash a ``(RequestContext, weakref[FastMCP])`` tuple — never a
    ``Context`` instance — because ``Context`` properties are themselves
    ContextVar-dependent and would resolve stale values in the receive
    loop.  Instead we construct a fresh ``Context`` here after restoring
    ``request_ctx``, so its property accesses read the correct values.

    This is a set-only repair of a long-lived task's ContextVars, not a
    scope: we never ``reset()`` because the prior values are stale and
    the loop keeps running.  ``_current_server`` is restored alongside
    ``_current_context`` so handlers that resolve the server via
    dependency injection (e.g. ``get_server()``) see the right instance;
    it is set directly rather than via ``Context.__aenter__`` to avoid
    opening a context-manager lifecycle on an unscoped path.
    """
    import weakref

    from fastmcp.server.context import Context, _current_context
    from fastmcp.server.dependencies import _current_server

    stashed = rc_ref[0]
    if stashed is None:
        return

    rc, fastmcp_ref = stashed
    current_rc = fastmcp_request_ctx.get()
    # Restore unless the stashed proxy context is already the active one.
    if current_rc is rc:
        return
    fastmcp_request_ctx.set(rc)
    fastmcp = fastmcp_ref()
    if fastmcp is not None:
        _current_context.set(Context(fastmcp))
        _current_server.set(weakref.ref(fastmcp))


def _make_restoring_handler(handler: Callable, rc_ref: list[Any]) -> Callable:
    """Wrap a proxy handler to restore request_ctx before delegating.

    The wrapper is a plain ``async def`` so it passes
    ``inspect.isfunction()`` checks in handler registration paths
    (e.g., ``create_roots_callback``).
    """

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        _restore_request_context(rc_ref)
        return await handler(*args, **kwargs)

    return wrapper


class ProxyClient(Client[ClientTransportT]):
    """A proxy client that forwards advanced interactions between a remote MCP server and the proxy's connected clients.

    Supports forwarding roots, sampling, elicitation, logging, and progress.

    The default forwarding handlers must resolve the *proxy's* request context so
    they relay server-initiated requests (roots/sampling/elicitation) back to the
    proxy's own connected client, not to the upstream server they are talking to.
    Under SDK v2 an in-memory backend runs in the same event loop as this client,
    so a naive ``get_context()`` inside a handler can resolve to the backend's
    context and forward the request straight back to the backend — an infinite
    loop. To avoid that, ``ProxyTool.run`` (and the other proxy components) stash
    the proxy-side ``RequestContext`` in ``_proxy_rc_ref`` before each backend
    call, and the handlers are wrapped to restore it before forwarding.
    """

    # Mutable list shared across copies (Client.new() uses copy.copy, which
    # preserves references to mutable containers). Proxy components write [0]
    # before each backend call; handlers read it to restore the proxy's
    # request_ctx before forwarding. Stores a (RequestContext, weakref[FastMCP])
    # tuple — never a Context instance — because Context properties are
    # ContextVar-dependent and would resolve stale values in the receive loop.
    _proxy_rc_ref: list[Any]
    _proxy_restoring_handler_keys: set[str]

    # A proxy forwards calls; it must not advertise task support to its backend.
    # Proxied tools run synchronously (forbidden mode), and the proxy has no path
    # to drive a backend task on the front connection's behalf, so the internal
    # tasks client extension is not folded into a proxy's backend client.
    _auto_internal_extensions: bool = False

    def __init__(
        self,
        transport: ClientTransportT
        | FastMCP[Any]
        | SDKServer
        | AnyUrl
        | Path
        | MCPConfig
        | dict[str, Any]
        | str,
        **kwargs,
    ):
        if "name" not in kwargs:
            kwargs["name"] = self.generate_name()
        # ProxyClient itself defaults to the handshake era when constructed
        # directly: a single proxy session can only be one era, and handshake
        # keeps the server-initiated push forwarding (sampling / elicitation /
        # roots, via the handlers installed below) that proxies rely on. When a
        # proxy is created from a non-Client target (`create_proxy(target)` /
        # `_create_client_factory`) with no explicit mode, the factory instead
        # MIRRORS the front connection's negotiated era onto this client per
        # request, so the whole chain speaks one era end-to-end. An explicit
        # `mode=` (e.g. `create_proxy(target, mode="auto")`) pins the era and
        # overrides mirroring. The eras are mutually exclusive per session.
        #
        # The handshake default is pinned explicitly rather than inherited from
        # `Client`, whose own default is `"auto"`: mirroring only applies when
        # there is a front request to mirror, so this is the fallback for a
        # directly-constructed ProxyClient, and it must not drift with the
        # client default.
        kwargs.setdefault("mode", "legacy")
        # Install context-restoring handler wrappers BEFORE super().__init__
        # registers them with the Client's session kwargs.
        self._proxy_rc_ref = [None]
        self._proxy_restoring_handler_keys = set()
        for key, default_fn in (
            ("roots", default_proxy_roots_handler),
            ("sampling_handler", default_proxy_sampling_handler),
            ("elicitation_handler", default_proxy_elicitation_handler),
            ("log_handler", default_proxy_log_handler),
            ("progress_handler", default_proxy_progress_handler),
        ):
            if key not in kwargs:
                kwargs[key] = _make_restoring_handler(default_fn, self._proxy_rc_ref)
                self._proxy_restoring_handler_keys.add(key)
        super().__init__(transport=transport, **kwargs)  # ty: ignore[no-matching-overload]

        self._transport_options = PROXY_TRANSPORT_OPTIONS

    def _bind_restoring_handlers(self) -> None:
        if "roots" in self._proxy_restoring_handler_keys:
            self._session_kwargs["list_roots_callback"] = create_roots_callback(
                _make_restoring_handler(default_proxy_roots_handler, self._proxy_rc_ref)
            )
        if "sampling_handler" in self._proxy_restoring_handler_keys:
            self._session_kwargs["sampling_callback"] = create_sampling_callback(
                _make_restoring_handler(
                    default_proxy_sampling_handler, self._proxy_rc_ref
                )
            )
        if "elicitation_handler" in self._proxy_restoring_handler_keys:
            self._session_kwargs["elicitation_callback"] = create_elicitation_callback(
                _make_restoring_handler(
                    default_proxy_elicitation_handler, self._proxy_rc_ref
                )
            )
        if "log_handler" in self._proxy_restoring_handler_keys:
            self._session_kwargs["logging_callback"] = create_log_callback(
                _make_restoring_handler(default_proxy_log_handler, self._proxy_rc_ref)
            )
        if "progress_handler" in self._proxy_restoring_handler_keys:
            self._progress_handler = _make_restoring_handler(
                default_proxy_progress_handler, self._proxy_rc_ref
            )

    def new(self) -> ProxyClient[ClientTransportT]:
        new_client = cast(ProxyClient[ClientTransportT], super().new())
        new_client._proxy_rc_ref = [None]
        new_client._proxy_restoring_handler_keys = set(
            self._proxy_restoring_handler_keys
        )
        new_client._bind_restoring_handlers()
        return new_client


class StatefulProxyClient(ProxyClient[ClientTransportT]):
    """A proxy client that provides a stateful client factory for the proxy server.

    The stateful proxy client bound its copy to the server session.
    And it will be disconnected when the session is exited.

    This is useful to proxy a stateful mcp server such as the Playwright MCP server.
    Note that it is essential to ensure that the proxy server itself is also stateful.

    The base ``ProxyClient`` already installs the context-restoring handlers
    (see its docstring); this subclass additionally caches one client per stable
    ``Connection`` and forces disconnect when the connection is torn down.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # SDK v2 constructs a ServerSession per request, so per-session keying
        # would build a fresh proxy client for every request. Key by the stable
        # per-connection `Connection` instead, and tie cleanup to its exit stack.
        self._caches: dict[Connection, Client[ClientTransportT]] = {}

    def new(self) -> StatefulProxyClient[ClientTransportT]:
        return cast(StatefulProxyClient[ClientTransportT], super().new())

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[override]  # ty:ignore[invalid-method-override]
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
        # SDK v2: the ServerSession is per-request; the Connection is the stable
        # per-connection object that owns the exit stack. Key the cache and the
        # cleanup callback off it so one proxy client is reused for the whole
        # connection instead of one per request.
        connection = getattr(session, "_connection", None)
        if connection is None:
            raise RuntimeError(
                "Stateful proxy requires a per-connection server session; "
                "no connection is available on the current context."
            )
        proxy_client = self._caches.get(connection, None)

        if proxy_client is None:
            proxy_client = self.new()
            logger.debug(f"{proxy_client} created for {connection}")
            self._caches[connection] = proxy_client

            async def _on_connection_exit():
                self._caches.pop(connection, None)
                logger.debug(f"{proxy_client} will be disconnect")
                # This callback runs while the connection's exit stack is
                # unwinding, which usually happens because the owning task is
                # being cancelled. Shield the disconnect so the forced cleanup
                # actually runs to completion instead of aborting at the first
                # cancellation checkpoint (e.g. acquiring the session lock).
                with anyio.CancelScope(shield=True):
                    await proxy_client._disconnect(force=True)

            connection.exit_stack.push_async_callback(_on_connection_exit)

        return proxy_client
