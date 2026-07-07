from __future__ import annotations

import weakref
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

import mcp_types
from mcp.server._otel import OpenTelemetryMiddleware
from mcp.server.context import (
    CallNext,
    HandlerResult,
    ServerMiddleware,
    ServerRequestContext,
)
from mcp.server.lowlevel.server import (
    LifespanResultT,
    NotificationOptions,
)
from mcp.server.lowlevel.server import (
    Server as _Server,
)
from mcp.server.models import InitializationOptions
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server as stdio_server
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from fastmcp.apps.config import UI_EXTENSION_ID
from fastmcp.server.telemetry import seam_span
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.middleware import CallNext as FastMCPCallNext
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


def client_supports_extension(session: ServerSession, extension_id: str) -> bool:
    """Check whether the connected client supports a given MCP extension.

    Inspects the ``extensions`` capability on ``ClientCapabilities`` sent by the
    client during initialization. In v2 the client's initialize params are
    reachable via ``session.client_params``.

    SDK v2 declares ``extensions`` as a real field on ``ClientCapabilities``, so
    a client sending ``ClientCapabilities(extensions={...})`` populates the field
    directly. We read that field first and fall back to ``model_extra`` only for
    legacy-serialized clients that carried ``extensions`` as an extra key.
    """
    client_params = session.client_params
    if client_params is None:
        return False
    caps = client_params.capabilities
    if caps is None:
        return False
    extensions: dict[str, Any] | None = caps.extensions
    if extensions is None:
        # Legacy fallback: clients that serialized `extensions` as an extra key
        # (ClientCapabilities uses extra="allow") rather than the real field.
        extras = caps.model_extra or {}
        extensions = extras.get("extensions")
    if not extensions:
        return False
    return extension_id in extensions


class FastMCPServerMiddleware:
    """SDK v2 server middleware that routes ``initialize`` through FastMCP middleware.

    v2 no longer lets FastMCP subclass ``ServerSession`` (the runner constructs
    it per request), so the old ``MiddlewareServerSession._received_request``
    override is replaced by a ``ServerMiddleware``. This middleware binds the
    FastMCP request-context ContextVar for the whole chain (covering
    ``initialize``, where no handler adapter runs) and routes the initialize
    request through the FastMCP middleware chain so ``on_initialize`` hooks fire
    and can observe the ``InitializeResult`` or veto with ``MCPError``.
    """

    def __init__(self, fastmcp: FastMCP):
        self._ref: weakref.ref[FastMCP] = weakref.ref(fastmcp)

    async def __call__(
        self, ctx: ServerRequestContext, call_next: CallNext
    ) -> HandlerResult:
        from fastmcp.server.dependencies import bind_request_context

        fastmcp = self._ref()
        with (
            self._apply_shared_context(fastmcp),
            bind_request_context(ctx),
            self._seam_span(fastmcp, ctx),
        ):
            # Only initialize requests (request_id present) go through FastMCP
            # middleware here; every other request already binds the context in
            # its own adapter, so we just pass through.
            if ctx.method == "initialize" and ctx.request_id is not None:
                if fastmcp is not None:
                    return await self._run_initialize_mw(fastmcp, ctx, call_next)
            return await call_next(ctx)

    @contextmanager
    def _seam_span(
        self, fastmcp: FastMCP | None, ctx: ServerRequestContext
    ) -> Iterator[None]:
        """Open the per-request SERVER span at the middleware seam.

        The removed SDK ``OpenTelemetryMiddleware`` produced one SERVER span per
        inbound message. FastMCP re-creates that guarantee for *every* request
        method here — the last place shared by all request methods regardless of
        how their handler is registered — so a request rejected *before* the
        high-level path (FastMCP middleware, auth check, not-found mapping,
        params failure) is still traced with an error span.

        For the high-level methods (tools/resources/prompts), the deep
        ``server_span(...)`` call in ``fastmcp.server.server`` detects this seam
        span as the active span and *enriches* it in place with component
        attributes instead of opening a second span, so there is exactly one
        richly-attributed SERVER span per request. Notifications
        (``request_id is None``) are skipped — a SERVER span models an inbound
        request/response, not a fire-and-forget notification.
        """
        if fastmcp is None or ctx.request_id is None:
            yield
            return
        with seam_span(ctx.method, fastmcp.name):
            yield

    @contextmanager
    def _apply_shared_context(self, fastmcp: FastMCP | None) -> Iterator[None]:
        """Re-establish app-scoped SharedContext ContextVars for this request.

        The SDK v2 dispatcher runs handlers in the message sender's context, so
        the ``SharedContext`` ContextVars set during the server lifespan are not
        visible here. Re-apply the lifespan's captured snapshot so ``Shared()``
        dependencies resolve (and stay shared) across requests.
        """
        snapshot = fastmcp._shared_context_snapshot if fastmcp is not None else None
        if not snapshot:
            yield
            return
        tokens = [(var, var.set(value)) for var, value in snapshot.items()]
        try:
            yield
        finally:
            for var, token in reversed(tokens):
                var.reset(token)

    async def _run_initialize_mw(
        self,
        fastmcp: FastMCP,
        ctx: ServerRequestContext,
        call_next: CallNext,
    ) -> HandlerResult:
        from fastmcp.server.context import Context
        from fastmcp.server.middleware.middleware import MiddlewareContext

        # Reconstruct the InitializeRequest from the raw params so FastMCP
        # middleware `on_initialize` hooks that inspect the message still work.
        init_message: mcp_types.InitializeRequest | None = None
        try:
            params = ctx.params if isinstance(ctx.params, dict) else {}
            init_message = mcp_types.InitializeRequest.model_validate(
                {"method": "initialize", "params": params}, by_name=False
            )
        except ValidationError:
            init_message = None

        # Track the initialize result produced by the SDK chain so a FastMCP
        # middleware that raises `MCPError` *after* `call_next` can be
        # logged-and-swallowed (the result is already committed) rather than
        # producing a duplicate error response — preserving the pre-v2 contract.
        captured_result: mcp_types.InitializeResult | None = None
        call_next_completed = False

        async def call_original_handler(
            _mw_ctx: MiddlewareContext,
        ) -> mcp_types.InitializeResult | None:
            # call_next(ctx) runs the rest of the SDK chain, which for
            # initialize returns the serialized InitializeResult dict. FastMCP
            # middleware `on_initialize` hooks expect a typed InitializeResult,
            # so deserialize before handing control back up the FastMCP chain.
            # The runner's `_dump_result` re-serializes whatever we return, so a
            # returned model round-trips cleanly.
            nonlocal captured_result, call_next_completed
            raw = await call_next(ctx)
            if isinstance(raw, mcp_types.InitializeResult):
                captured_result = raw
            elif isinstance(raw, Mapping):
                captured_result = mcp_types.InitializeResult.model_validate(dict(raw))
            call_next_completed = True
            return captured_result if raw is not None else None

        async with Context(fastmcp=fastmcp, session=ctx.session) as fastmcp_ctx:
            mw_context = MiddlewareContext(
                message=init_message,
                source="client",
                type="request",
                method="initialize",
                fastmcp_context=fastmcp_ctx,
            )
            try:
                return await fastmcp._run_middleware(
                    mw_context,
                    cast("FastMCPCallNext[Any, Any]", call_original_handler),
                )
            except MCPError:
                # A middleware raised after the initialize response was already
                # produced: log and return the committed result instead of
                # re-raising to avoid responding to initialize twice. If the
                # error was raised before `call_next` succeeded, re-raise so the
                # dispatcher turns it into the wire error.
                if not call_next_completed:
                    raise
                logger.warning(
                    "MCPError raised by FastMCP middleware after the initialize "
                    "response was produced; logging and not re-raising to avoid a "
                    "duplicate response.",
                    exc_info=True,
                )
                return captured_result


class LowLevelServer(_Server[LifespanResultT]):
    def __init__(self, fastmcp: FastMCP, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Store a weak reference to FastMCP to avoid circular references
        self._fastmcp_ref: weakref.ref[FastMCP] = weakref.ref(fastmcp)

        # FastMCP servers support notifications for all components. v2 derives
        # capabilities from registered handlers + protocol_version, but legacy
        # clients still read NotificationOptions at create_initialization_options
        # time, so keep a default here and pass it through.
        self.notification_options = NotificationOptions(
            prompts_changed=True,
            resources_changed=True,
            tools_changed=True,
        )

        # The SDK seeds `OpenTelemetryMiddleware` into `self.middleware` so every
        # lowlevel server emits a SERVER span per message. FastMCP emits its own,
        # richer SERVER span per request (see `fastmcp.server.telemetry`), so the
        # SDK's would produce a second, duplicate SERVER span with different
        # attribute conventions for every request. Drop it — match by type rather
        # than position so we don't depend on the SDK seeding it at index 0, and
        # leave any other seeded middleware intact. FastMCP's telemetry extracts
        # inbound W3C trace context from `_meta` itself, so distributed-trace
        # propagation is unaffected.
        self.middleware = [
            mw for mw in self.middleware if not isinstance(mw, OpenTelemetryMiddleware)
        ]

        # Route initialize through FastMCP middleware.
        self.middleware.append(
            cast("ServerMiddleware[LifespanResultT]", FastMCPServerMiddleware(fastmcp))
        )

    @property
    def fastmcp(self) -> FastMCP:
        """Get the FastMCP instance."""
        fastmcp = self._fastmcp_ref()
        if fastmcp is None:
            raise RuntimeError("FastMCP instance is no longer available")
        return fastmcp

    def create_initialization_options(
        self,
        notification_options: NotificationOptions | None = None,
        experimental_capabilities: dict[str, dict[str, Any]] | None = None,
        extensions: dict[str, dict[str, Any]] | None = None,
    ) -> InitializationOptions:
        # ensure we use the FastMCP notification options
        if notification_options is None:
            notification_options = self.notification_options
        merged = {
            **self.fastmcp.experimental_capabilities,
            **(experimental_capabilities or {}),
        }
        return super().create_initialization_options(
            notification_options=notification_options,
            experimental_capabilities=merged or None,
            extensions=extensions,
        )

    def get_capabilities(
        self,
        notification_options: NotificationOptions | None = None,
        experimental_capabilities: dict[str, dict[str, Any]] | None = None,
        extensions: dict[str, dict[str, Any]] | None = None,
        *,
        protocol_version: str | None = None,
    ) -> mcp_types.ServerCapabilities:
        """Override to set capabilities.tasks as a first-class field per SEP-1686
        and advertise the MCP Apps UI extension.

        ``ServerCapabilities.tasks`` and ``ServerCapabilities.extensions`` are
        real declared fields in v2, so we update them directly.
        """
        from fastmcp.server.tasks.capabilities import get_task_capabilities

        capabilities = super().get_capabilities(
            notification_options,
            experimental_capabilities,
            extensions,
            protocol_version=protocol_version,
        )

        existing_extensions = capabilities.extensions or {}
        return capabilities.model_copy(
            update={
                "tasks": get_task_capabilities(),
                "extensions": {**existing_extensions, UI_EXTENSION_ID: {}},
            }
        )
