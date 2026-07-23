from __future__ import annotations

import weakref
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
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
from mcp.server.request_state import RequestStateBoundary, RequestStateSecurity
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

# The request methods that FastMCP serves through a handler adapter, each of which
# runs the FastMCP middleware chain interior (see MCPOperationsMixin). The root
# dispatch leaves these to the interior dispatch and only observes them if they fail before
# reaching it. Every other message is dispatched here at the root.
_INTERIOR_METHODS = frozenset(
    {
        "tools/call",
        "tools/list",
        "resources/read",
        "resources/list",
        "resources/templates/list",
        "prompts/get",
        "prompts/list",
    }
)


def _raw_message(ctx: ServerRequestContext) -> Any:
    """The message payload handed to the root dispatch's ``on_message``/``on_request`` pass.

    The raw inbound params mapping is used verbatim rather than a validated,
    typed request model. This is deliberate: the outer pass must observe *every*
    message, including malformed or unroutable ones, and reconstructing a typed
    model would raise on exactly those messages and hide them from the hooks.
    The method and request/notification kind are carried on the
    ``MiddlewareContext`` itself, so observation middleware still has everything
    it needs.
    """
    params = ctx.params
    if isinstance(params, Mapping):
        return dict(params)
    return {} if params is None else params


def _forward_ctx(
    ctx: ServerRequestContext, mw_ctx: Any, original: Any
) -> ServerRequestContext:
    """Fold middleware edits to the *message* back into the SDK context.

    The outer pass hands middleware a *copy* of the raw params (see
    ``_raw_message``), so a hook that follows the documented inspect/modify
    contract — mutating ``context.message`` or passing ``context.copy(message=...)``
    to ``call_next`` — would otherwise have its edits silently dropped when the
    bridge dispatched the original context. Rewriting through
    ``dataclasses.replace`` is how the SDK documents altering what the handler
    sees. An untouched message forwards the original context unchanged.

    ``ctx.method`` is deliberately *not* rewritable here. Dispatch has already
    branched on the method to decide that this message has no interior handler,
    so redirecting it now — say, turning a ``ping`` into a ``tools/list`` — would
    hand it to a component handler that runs the FastMCP chain a second time,
    firing ``on_message`` and raw ``__call__`` overrides twice for one message
    and duplicating whatever side effects (rate limiting, authorization,
    logging) they carry. Rewriting the method is not part of the documented
    middleware contract; only the message is.
    """
    message = mw_ctx.message
    if isinstance(message, Mapping):
        params: Mapping[str, Any] | None = dict(message)
        # `_raw_message` renders absent params as `{}`; keep that distinction so
        # an untouched notification still dispatches with `params=None`.
        if ctx.params is None and message == original and not message:
            params = None
    else:
        params = ctx.params
    if params == ctx.params:
        return ctx
    return replace(ctx, params=params)


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
    """Root dispatch for the FastMCP middleware chain, in the SDK's middleware layer.

    v2 no longer lets FastMCP subclass ``ServerSession`` (the runner constructs
    it per request), so the old ``MiddlewareServerSession._received_request``
    override is replaced by a ``ServerMiddleware`` — an ordinary entry in the
    SDK's own middleware list. Sitting at the root of dispatch, this
    is the single entry point through which *every* inbound message flows —
    requests, notifications, cancellations, ``initialize``, and even malformed or
    unroutable messages the SDK can still hand us. It binds the FastMCP
    request-context ContextVar and re-applies the app-scoped ``SharedContext`` for
    the whole chain, then runs the FastMCP ``Middleware`` chain so
    ``on_message`` / ``on_request`` / ``on_notification`` observe the message.

    Dispatch shapes:

    - ``initialize`` runs the *whole* FastMCP chain here (``on_message`` ->
      ``on_request`` -> ``on_initialize``) because there is no interior handler
      adapter for it: the SDK builds the ``InitializeResult`` directly, so this is
      the only place ``on_initialize`` can observe it or veto with ``MCPError``.
    - The component methods (``tools/call``, ``tools/list``, ``resources/read``,
      ...) still run their FastMCP chain *interior*, in the handler adapter, where
      ``on_call_tool`` receives the typed component result and a tool exception
      propagates through ``on_message``/``on_request`` exactly where the built-in
      error/logging/timing middleware expect it. The root dispatch does not re-run the
      chain for these — it only steps in when such a request fails *before* the
      interior runs (malformed params, routing), so ``on_message`` still observes
      the failure.
    - Every other message — all notifications (including ``notifications/cancelled``
      and ``notifications/initialized``), ``ping``, ``logging/setLevel``, and any
      unroutable/non-component request — has no interior FastMCP dispatch, so the
      root dispatch runs the ``"outer"`` pass (``on_message`` plus
      ``on_request``/``on_notification``) here, wrapping the real SDK dispatch.
      This closes the long-standing gap where these messages were invisible to
      FastMCP middleware.
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
            if fastmcp is None:
                return await call_next(ctx)
            if ctx.method == "initialize" and ctx.request_id is not None:
                return await self._run_initialize_mw(fastmcp, ctx, call_next)
            if ctx.request_id is not None and ctx.method in _INTERIOR_METHODS:
                return await self._dispatch_component(fastmcp, ctx, call_next)
            return await self._run_outer_mw(fastmcp, ctx, call_next, _raise=None)

    async def _dispatch_component(
        self,
        fastmcp: FastMCP,
        ctx: ServerRequestContext,
        call_next: CallNext,
    ) -> HandlerResult:
        """Delegate a component request to the interior chain, covering early failures.

        The interior handler adapter runs the FastMCP chain itself and records
        ``_interior_dispatched``. If the request instead fails before reaching it
        (malformed params, method routing), the flag stays False and no hook fired
        — so the root dispatch runs the ``"outer"`` pass to observe the failure, re-raising
        the original error inside it so ``on_message``/``on_request`` see it.
        """
        from fastmcp.server.middleware.middleware import _interior_dispatched

        token = _interior_dispatched.set(False)
        try:
            return await call_next(ctx)
        except (MCPError, ValidationError) as exc:
            if _interior_dispatched.get():
                raise
            return await self._run_outer_mw(fastmcp, ctx, call_next, _raise=exc)
        finally:
            _interior_dispatched.reset(token)

    async def _run_outer_mw(
        self,
        fastmcp: FastMCP,
        ctx: ServerRequestContext,
        call_next: CallNext,
        *,
        _raise: BaseException | None,
    ) -> HandlerResult:
        """Run the method-agnostic (``on_message``/``on_request``) hook pass.

        ``call_next`` bridges to the real SDK dispatch (request-state boundary,
        params validation, the notification handler), so these hooks observe the
        actual wire outcome: a notification returns ``None``, an unroutable request
        raises through ``call_next``. Message edits are folded back in through
        ``_forward_ctx``.

        When ``_raise`` is set the operation already failed before the interior
        ran, and the bridge re-raises it rather than dispatching. This pass is
        the *observation* path for that failure, not a retry: re-dispatching a
        corrected component request would run its handler, which runs the FastMCP
        chain interior, firing ``on_message`` and the raw ``__call__`` override a
        second time for one message. A hook cannot repair a malformed
        ``tools/call`` from here — it sees the failure, and the failure stands.
        """
        from fastmcp.server.context import Context
        from fastmcp.server.middleware.middleware import MiddlewareContext

        is_notification = ctx.request_id is None
        original_message = _raw_message(ctx)

        async def root_call_next(_mw_ctx: MiddlewareContext) -> HandlerResult:
            if _raise is not None:
                raise _raise
            return await call_next(_forward_ctx(ctx, _mw_ctx, original_message))

        async with Context(fastmcp=fastmcp, session=ctx.session) as fastmcp_ctx:
            mw_context = MiddlewareContext(
                message=original_message,
                source="client",
                type="notification" if is_notification else "request",
                method=ctx.method,
                fastmcp_context=fastmcp_ctx,
            )
            return await fastmcp._run_middleware(
                mw_context,
                cast("FastMCPCallNext[Any, Any]", root_call_next),
                phase="outer",
            )

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

        # Install the SDK's request-state boundary (SEP-2322): it seals every
        # outgoing `InputRequiredResult.request_state` at the wire and unseals
        # every inbound echo *before* any handler runs, so tool bodies only ever
        # see plaintext `ctx.request_state`. Mirrors `MCPServer.__init__`. When
        # the server was constructed without an explicit `request_state_security`
        # policy, seal under a per-process ephemeral key (single-process
        # deployments); multi-replica deployments pass a shared-key policy. The
        # low-level server always has a name (FastMCP autogenerates one), so the
        # audience claim is always populated.
        security = fastmcp._request_state_security or RequestStateSecurity.ephemeral()
        self.middleware.append(
            cast(
                "ServerMiddleware[LifespanResultT]",
                RequestStateBoundary(security, default_audience=self.name),
            )
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
        return super().create_initialization_options(
            notification_options=notification_options,
            experimental_capabilities=experimental_capabilities,
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
        real declared fields in v2, so we update them directly. The
        `FastMCP(experimental_capabilities=...)` merge also lives here rather
        than in `create_initialization_options`: the modern `server/discover`
        handler calls this directly, without going through
        `create_initialization_options` at all, so merging there only reached
        the handshake-era `initialize` response and silently dropped
        constructor-configured experimental capabilities from `discover`.
        """
        from fastmcp.server.tasks.capabilities import get_task_capabilities

        merged_experimental = {
            **self.fastmcp.experimental_capabilities,
            **(experimental_capabilities or {}),
        }
        capabilities = super().get_capabilities(
            notification_options,
            merged_experimental or None,
            extensions,
            protocol_version=protocol_version,
        )

        # Advertise every registered extension's settings under
        # capabilities.extensions[identifier]. The hand-rolled UI splice stays
        # for now (MCP Apps migrates onto the extension API in a later phase);
        # the two coexist. Advertisement is unconditional — the SDK's pre-2026
        # version sieve strips capabilities.extensions on legacy eras, a known
        # limitation (sdk-feedback #2).
        existing_extensions = capabilities.extensions or {}
        registered_extensions = {
            extension.identifier: extension.settings()
            for extension in self.fastmcp._extensions.values()
        }
        return capabilities.model_copy(
            update={
                "tasks": get_task_capabilities(),
                "extensions": {
                    **existing_extensions,
                    UI_EXTENSION_ID: {},
                    **registered_extensions,
                },
            }
        )
