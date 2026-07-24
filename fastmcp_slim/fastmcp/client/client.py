from __future__ import annotations

import asyncio
import copy
import datetime
import hashlib
import secrets
import ssl
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast, overload

import anyio
import anyio.lowlevel
import httpx2
import mcp_types
from exceptiongroup import catch
from mcp import ClientSession, MCPError
from mcp.client._input_required import (
    DEFAULT_INPUT_REQUIRED_MAX_ROUNDS,
    run_input_required_driver,
)
from mcp.client._probe import negotiate_auto
from mcp.client.caching import (
    CacheConfig,
    CacheMode,
    ClientResponseCache,
    InMemoryResponseCacheStore,
)
from mcp.client.client import (
    _evicting_message_handler,
    _fold_extensions,
    _synthesize_discover,
)
from mcp.client.extension import (
    ClaimContext,
    ClientExtension,
    NotificationBinding,
    ResultClaim,
)
from mcp.client.session import (
    ClientRequestContext,
    ElicitationFnT,
    MessageHandlerFnT,
)
from mcp_types.methods import validate_server_result
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS
from pydantic import AnyUrl, ValidationError

import fastmcp as fastmcp
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.elicitation import (
    ElicitationHandler,
    create_elicitation_callback,
)
from fastmcp.client.extension_hooks import build_internal_client_extensions
from fastmcp.client.logging import (
    LogHandler,
    create_log_callback,
    default_log_handler,
)
from fastmcp.client.messages import MessageHandler, MessageHandlerT
from fastmcp.client.mixins import (
    ClientPromptsMixin,
    ClientResourcesMixin,
    ClientToolsMixin,
)
from fastmcp.client.progress import ProgressHandler, default_progress_handler
from fastmcp.client.roots import (
    RootsHandler,
    RootsList,
    create_roots_callback,
)
from fastmcp.client.sampling import (
    SamplingHandler,
    create_sampling_callback,
)
from fastmcp.mcp_config import MCPConfig
from fastmcp.utilities.exceptions import get_catch_handlers
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.timeout import normalize_timeout_to_seconds

if TYPE_CHECKING:
    from fastmcp.server import FastMCP
else:
    FastMCP = Any

from .transports import (
    ClientTransport,
    ClientTransportT,
    FastMCPTransport,
    MCPConfigTransport,
    NodeStdioTransport,
    PythonStdioTransport,
    SDKServer,
    SessionKwargs,
    SSETransport,
    StreamableHttpTransport,
    infer_transport,
)
from .transports.base import TransportOptions

__all__ = [
    "Client",
    "ElicitationHandler",
    "LogHandler",
    "MessageHandler",
    "ProgressHandler",
    "RootsHandler",
    "RootsList",
    "SamplingHandler",
    "SessionKwargs",
]

logger = get_logger(__name__)

T = TypeVar("T", bound="ClientTransport")
ResultT = TypeVar("ResultT")
CacheableT = TypeVar("CacheableT", bound=mcp_types.CacheableResult)

ConnectMode = Literal["legacy", "auto"] | str
"""How the client negotiates the protocol era at connect time.

- ``"auto"`` (the default): probe ``server/discover`` at the newest modern version and
  adopt it, falling back to the initialize handshake for any server that is not positive
  evidence of a modern peer (a denylist fallback — see the SDK's ``negotiate_auto``).
- ``"legacy"``: the classic initialize handshake, byte-identical to pre-v4 behavior for
  handshake-era servers. Opt into this to force the old handshake.
- a modern protocol-version string (e.g. ``"2026-07-28"``): adopt that version directly
  without probing, synthesizing a minimal ``DiscoverResult`` when none is supplied.

The ``str`` arm is only for the version-pin case; ``Client.__init__`` rejects any other value.
"""


@asynccontextmanager
async def _conformant_discover_only(
    session: ClientSession,
) -> AsyncIterator[None]:
    """Hold ``session.send_discover`` to the same wire schema every later reply must meet.

    ``negotiate_auto`` accepts a probe that parses as the version-free
    ``DiscoverResult``, whose ``resultType``/``ttlMs``/``cacheScope`` all carry
    SDK-side defaults. Every request *after* adoption is instead checked against
    the strict per-version surface (``validate_server_result``), where those same
    three fields are required. A server that answers ``server/discover`` without
    them therefore passes the probe and then fails every subsequent call — the
    connection is adopted into an era the peer cannot actually serve.

    Closing that gap means judging the probe by the rule that will govern the rest
    of the connection. A result that would be rejected later is not positive
    evidence of a modern peer, so it is reported as an ordinary probe failure and
    ``negotiate_auto`` falls back to the initialize handshake, exactly as it does
    for a server with no ``server/discover`` at all.
    """
    send_discover = session.send_discover

    async def _checked_send_discover(version: str) -> dict[str, Any]:
        raw = await send_discover(version)
        try:
            validate_server_result("server/discover", version, raw)
        except ValidationError as e:
            # Ordered before the ValueError arm below: pydantic's ValidationError
            # subclasses ValueError, so a broader clause first would swallow it.
            logger.debug(
                "server/discover at %s is not %s-conformant (%s); "
                "falling back to the initialize handshake",
                version,
                version,
                e,
            )
            raise MCPError(
                code=mcp_types.INVALID_PARAMS,
                message=(
                    f"server/discover result is not conformant with {version}; "
                    "treating the server as handshake-era"
                ),
            ) from e
        except (KeyError, ValueError):
            # No schema on file for this method/version pair, so there is nothing to
            # judge the probe against; leave the verdict to negotiate_auto's parse.
            return raw
        return raw

    # A transport may itself have installed a `send_discover` override, so restore
    # whatever was there rather than assuming the class attribute.
    had_own = "send_discover" in vars(session)
    session.send_discover = _checked_send_discover  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        if had_own:
            session.send_discover = send_discover  # ty: ignore[invalid-assignment]
        else:
            del session.send_discover


@dataclass
class _FoldedExtensions:
    """`Client(extensions=...)` folded into the shapes `ClientSession` consumes.

    `ad` maps each extension identifier to its advertised settings (the SEP-2133
    capability ad), `claims` maps each identifier to its `ResultClaim`s, `bindings`
    is the flat list of `NotificationBinding`s the extensions observe, and `by_model`
    indexes every claim by its result model so a claimed `tools/call` result can be
    routed back to the owning resolver.
    """

    ad: dict[str, dict[str, Any]]
    claims: dict[str, tuple[ResultClaim[Any], ...]]
    bindings: list[NotificationBinding[Any]]
    by_model: dict[type[mcp_types.Result], ResultClaim[Any]]


@dataclass
class ClientSessionState:
    """Holds all session-related state for a Client instance.

    This allows clean separation of configuration (which is copied) from
    session state (which should be fresh for each new client instance).
    """

    session: ClientSession | None = None
    nesting_counter: int = 0
    lock: anyio.Lock = field(default_factory=anyio.Lock)
    session_task: asyncio.Task | None = None
    ready_event: anyio.Event = field(default_factory=anyio.Event)
    stop_event: anyio.Event = field(default_factory=anyio.Event)
    initialize_result: mcp_types.InitializeResult | None = None


@dataclass
class CallToolResult:
    """Parsed result from a tool call."""

    content: list[mcp_types.ContentBlock]
    structured_content: dict[str, Any] | None
    meta: dict[str, Any] | None
    data: Any = None
    is_error: bool = False


class Client(
    Generic[ClientTransportT],
    ClientResourcesMixin,
    ClientPromptsMixin,
    ClientToolsMixin,
):
    """
    MCP client that delegates connection management to a Transport instance.

    The Client class is responsible for MCP protocol logic, while the Transport
    handles connection establishment and management. Client provides methods for
    working with resources, prompts, tools and other MCP capabilities.

    This client supports reentrant context managers (multiple concurrent
    `async with client:` blocks) using reference counting and background session
    management. This allows efficient session reuse in any scenario with
    nested or concurrent client usage.

    MCP SDK 1.10 introduced automatic list_tools() calls during call_tool()
    execution. This created a race condition where events could be reset while
    other tasks were waiting on them, causing deadlocks. The issue was exposed
    in proxy scenarios but affects any reentrant usage.

    The solution uses reference counting to track active context managers,
    a background task to manage the session lifecycle, events to coordinate
    between tasks, and ensures all session state changes happen within a lock.
    Events are only created when needed, never reset outside locks.

    This design prevents race conditions where tasks wait on events that get
    replaced by other tasks, ensuring reliable coordination in concurrent scenarios.

    Args:
        transport:
            Connection source specification, which can be:

                - ClientTransport: Direct transport instance
                - FastMCP: In-process FastMCP server
                - AnyUrl or str: URL to connect to
                - Path: File path for local socket
                - MCPConfig: MCP server configuration
                - dict: Transport configuration

        roots: Optional RootsList or RootsHandler for filesystem access
        sampling_handler: Optional handler for sampling requests
        log_handler: Optional handler for log messages
        message_handler: Optional handler for protocol messages
        progress_handler: Optional handler for progress notifications
        timeout: Optional timeout for requests (seconds or timedelta)
        init_timeout: Optional timeout for initial connection (seconds or timedelta).
            Set to 0 to disable. If None, uses the value in the FastMCP global settings.
        mode: Protocol-era negotiation at connect time. `"auto"` (the default) probes
            `server/discover` and negotiates the modern era, denylist-falling-back to the
            initialize handshake for any server that is not positive evidence of a modern
            peer — safe against a mixed fleet of legacy and modern servers. `"legacy"`
            forces the initialize handshake, byte-identical to pre-v4 behavior; opt into it
            to pin the old handshake. A modern version string (e.g. `"2026-07-28"`) adopts
            that version directly without a probe.
        prior_discover: A previously obtained `DiscoverResult` to adopt when `mode` is a
            version pin, reused instead of synthesizing a minimal one. Ignored otherwise.
        input_required_max_rounds: Cap on `InputRequiredResult` (SEP-2322) retry rounds
            for `call_tool` / `get_prompt` / `read_resource` before the driver gives up.
            Only reachable on 2026-era servers that emit `InputRequiredResult`.
        cache: Client-side response caching (SEP-2549), opt-in. `None` (default) and
            `False` disable it; `True` enables the default in-memory store honoring
            server `ttlMs`/`cacheScope` hints; a `CacheConfig` customizes it. Honoring is
            modern-only, so a cache is inert on legacy connections. A custom `CacheConfig`
            store requires `target_id`, since FastMCP transports expose no server URL to
            derive a shared-store identity from.
        extensions: Opt-in client extensions (SEP-2133), a sequence of
            `mcp.client.extension.ClientExtension` instances. Each contributes its
            capability advertisement, its result claims, and its notification bindings,
            all of which are threaded into the underlying session. User-supplied
            notification bindings compose with FastMCP's internal task-status binding
            rather than replacing it. A claimed `call_tool` result is resolved
            transparently through the owning extension's resolver. For an advertise-only
            entry, use `mcp.client.advertise(identifier, settings)`.
        result_claims: Additional `ResultClaim`s (SEP-2133) keyed by the identifier of
            an extension already advertised through `extensions`, merged with that
            extension's own claims. Rarely needed directly; prefer declaring claims on
            the extension itself. Claimed shapes are modern-only and inert on a legacy
            connection.

    Examples:
        ```python
        # Connect to FastMCP server
        client = Client("http://localhost:8080")

        async with client:
            # List available resources
            resources = await client.list_resources()

            # Call a tool
            result = await client.call_tool("my_tool", {"param": "value"})
        ```
    """

    #: Whether FastMCP-internal client extensions (e.g. the tasks extension) are
    #: folded in automatically at construction. `ProxyClient` overrides this to
    #: `False`: a proxy forwards calls and must not advertise task support to its
    #: backend, since proxied tools run synchronously (forbidden mode) and the
    #: proxy has no path to drive a backend task on the front connection's behalf.
    _auto_internal_extensions: bool = True

    @overload
    def __init__(self: Client[T], transport: T, *args: Any, **kwargs: Any) -> None: ...

    @overload
    def __init__(
        self: Client[SSETransport | StreamableHttpTransport],
        transport: AnyUrl,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: Client[FastMCPTransport],
        transport: FastMCP | SDKServer,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: Client[PythonStdioTransport | NodeStdioTransport],
        transport: Path,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: Client[MCPConfigTransport],
        transport: MCPConfig | dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: Client[
            PythonStdioTransport
            | NodeStdioTransport
            | SSETransport
            | StreamableHttpTransport
        ],
        transport: str,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    def __init__(
        self,
        transport: (
            ClientTransportT
            | FastMCP
            | SDKServer
            | AnyUrl
            | Path
            | MCPConfig
            | dict[str, Any]
            | str
        ),
        name: str | None = None,
        roots: RootsList | RootsHandler | None = None,
        sampling_handler: SamplingHandler | None = None,
        sampling_capabilities: mcp_types.SamplingCapability | None = None,
        elicitation_handler: ElicitationHandler | None = None,
        log_handler: LogHandler | None = None,
        message_handler: MessageHandlerT | MessageHandler | None = None,
        progress_handler: ProgressHandler | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        auto_initialize: bool = True,
        init_timeout: datetime.timedelta | float | int | None = None,
        client_info: mcp_types.Implementation | None = None,
        auth: httpx2.Auth | Literal["oauth"] | str | None = None,
        verify: ssl.SSLContext | bool | str | None = None,
        mode: ConnectMode = "auto",
        prior_discover: mcp_types.DiscoverResult | None = None,
        input_required_max_rounds: int = DEFAULT_INPUT_REQUIRED_MAX_ROUNDS,
        cache: CacheConfig | bool | None = None,
        extensions: Sequence[ClientExtension] | None = None,
        result_claims: Mapping[str, Sequence[ResultClaim[Any]]] | None = None,
    ) -> None:
        self.name = name or self.generate_name()

        self.input_required_max_rounds = input_required_max_rounds

        if mode not in ("legacy", "auto") and mode not in MODERN_PROTOCOL_VERSIONS:
            hint = (
                f" ({mode!r} is a handshake-era version; use mode='legacy')"
                if mode in HANDSHAKE_PROTOCOL_VERSIONS
                else ""
            )
            raise ValueError(
                "mode must be 'legacy', 'auto', or one of "
                f"{list(MODERN_PROTOCOL_VERSIONS)}; got {mode!r}{hint}"
            )
        self.mode: ConnectMode = mode
        self._prior_discover = prior_discover

        self.transport = cast(ClientTransportT, infer_transport(transport))

        if verify is not None:
            from fastmcp.client.transports.http import StreamableHttpTransport
            from fastmcp.client.transports.sse import SSETransport

            if isinstance(self.transport, StreamableHttpTransport | SSETransport):
                self.transport.verify = verify
                # Re-sync existing OAuth auth with the new verify setting,
                # but only if the transport doesn't have a custom factory
                # (which takes precedence and was already applied to OAuth).
                if (
                    isinstance(self.transport.auth, OAuth)
                    and auth is None
                    and self.transport.httpx_client_factory is None
                ):
                    verify_factory = self.transport._make_verify_factory()
                    if verify_factory is not None:
                        self.transport.auth.httpx_client_factory = verify_factory
            else:
                raise ValueError(
                    "The 'verify' parameter is only supported for HTTP transports."
                )

        if auth is not None:
            self.transport._set_auth(auth)

        if log_handler is None:
            log_handler = default_log_handler

        if progress_handler is None:
            progress_handler = default_progress_handler

        self._progress_handler = progress_handler

        # Convert request timeout to float seconds (0 means disabled -> None)
        read_timeout_seconds = normalize_timeout_to_seconds(timeout)

        # handle init handshake timeout (0 means disabled)
        if init_timeout is None:
            init_timeout = fastmcp.settings.client_init_timeout
        self._init_timeout = normalize_timeout_to_seconds(init_timeout)

        self.auto_initialize = auto_initialize

        # Client-side response cache (SEP-2549). Only honored at modern protocol
        # versions (the SDK ClientResponseCache gates hint-reading on the negotiated
        # era), so it is inert for legacy connections. `cache=False` disables it.
        # Retained so `new()` can rebuild an independent cache per clone.
        self._cache_arg: CacheConfig | bool | None = cache
        self._response_cache: ClientResponseCache | None = self._build_response_cache(
            cache
        )

        # The unwrapped base handler (a user handler is preserved as-is).
        # Retained so `new()` can rebuild the clone's handler without unwrapping
        # the cache-eviction wrapper below.
        self._base_message_handler: MessageHandlerFnT | None = message_handler
        effective_message_handler = self._base_message_handler
        if self._response_cache is not None:
            effective_message_handler = _evicting_message_handler(
                self._response_cache, effective_message_handler
            )

        # Opt-in client extensions (SEP-2133) and their result claims. Retained so
        # `new()` can rebuild an independent set of session kwargs per clone.
        self._extensions_arg = extensions
        self._result_claims_arg = result_claims
        # Model→claim index the resolution path uses; (re)built by
        # `_build_extension_kwargs`.
        self._claim_by_model: dict[type[mcp_types.Result], ResultClaim[Any]] = {}

        # Build the elicitation callback up front: it is threaded both into the
        # session (to answer server-initiated elicitation) and into the internal
        # client extensions (so a task resolver can answer in-task input), and
        # `_build_extension_kwargs` — called below — needs it.
        self._elicitation_callback: ElicitationFnT | None = (
            create_elicitation_callback(elicitation_handler)
            if elicitation_handler is not None
            else None
        )

        self._session_kwargs: SessionKwargs = {
            "sampling_callback": None,
            "list_roots_callback": None,
            "logging_callback": create_log_callback(log_handler),
            "message_handler": effective_message_handler,
            "read_timeout_seconds": read_timeout_seconds,
            "client_info": client_info,
            **self._build_extension_kwargs(),
        }

        if roots is not None:
            self.set_roots(roots)

        if sampling_handler is not None:
            self._session_kwargs["sampling_callback"] = create_sampling_callback(
                sampling_handler
            )
            self._session_kwargs["sampling_capabilities"] = (
                sampling_capabilities
                if sampling_capabilities is not None
                else mcp_types.SamplingCapability()
            )

        if self._elicitation_callback is not None:
            self._session_kwargs["elicitation_callback"] = self._elicitation_callback

        # Maximum time to wait for a clean disconnect before giving up.
        # Normally disconnects complete in <100ms; this is a safety net for
        # unresponsive servers.
        self._disconnect_timeout: float = fastmcp.settings.client_disconnect_timeout

        # Session context management - see class docstring for detailed explanation
        self._session_state = ClientSessionState()
        self._transport_options: TransportOptions | None = None

    def _build_response_cache(
        self, cache: CacheConfig | bool | None
    ) -> ClientResponseCache | None:
        """Build the SEP-2549 response cache from the `cache=` argument.

        Response caching is opt-in: `None` (the default) and `False` both leave it
        disabled, so a legacy connection is byte-identical to pre-v4 behavior (no
        message-handler wrapping, no caching). `True` enables it with the default
        `CacheConfig` (honoring server `ttlMs`/`cacheScope` hints via a per-client
        in-memory store); a `CacheConfig` customizes it.

        Our transports abstract away the server URL the SDK Client uses to derive a
        cache identity, so `target_id` comes from the explicit `CacheConfig.target_id`
        or a random per-client id — meaning a custom shared store cannot serve one
        client's entries to another (documented on the parameter).
        """
        if cache is None or cache is False:
            return None
        config = cache if isinstance(cache, CacheConfig) else CacheConfig()

        target_id = config.target_id
        if target_id is None:
            if config.store is not None:
                raise ValueError(
                    "a custom cache store requires CacheConfig.target_id for FastMCP "
                    "transports: the server URL the SDK derives an identity from is not "
                    "available here, so entries in a shared store could never be served "
                    "to another client"
                )
            target_id = uuid.uuid4().hex

        return ClientResponseCache(
            store=config.store
            if config.store is not None
            else InMemoryResponseCacheStore(),
            partition=config.partition,
            arm_id=hashlib.sha256(target_id.encode()).hexdigest(),
            default_ttl_ms=config.default_ttl_ms,
            clock=config.clock,
            share_public=config.share_public,
            # Lazy: the negotiated version is unknown until the handshake completes.
            negotiated_version=lambda: (
                self._session_state.session.protocol_version
                if self._session_state.session is not None
                else None
            ),
        )

    def _reset_session_state(self, full: bool = False) -> None:
        """Reset session state after disconnect or cancellation.

        Args:
            full: If True, also resets session_task and nesting_counter.
                  Use full=True for cancellation cleanup where the session
                  task was started but never completed normally.
        """
        self._session_state.session = None
        self._session_state.initialize_result = None
        if full:
            self._session_state.session_task = None
            self._session_state.nesting_counter = 0

    @property
    def session(self) -> ClientSession:
        """Get the current active session. Raises RuntimeError if not connected."""
        if self._session_state.session is None:
            raise RuntimeError(
                "Client is not connected. Use the 'async with client:' context manager first."
            )

        return self._session_state.session

    @property
    def initialize_result(self) -> mcp_types.InitializeResult | None:
        """Get the result of the initialization request.

        `None` on a modern (`server/discover`) connection, which negotiates via a
        `DiscoverResult` rather than an `InitializeResult`. Use `protocol_version`,
        `server_info`, `server_capabilities`, and `instructions` for era-neutral
        access to the negotiated server metadata.
        """
        return self._session_state.initialize_result

    @property
    def protocol_version(self) -> str | None:
        """The negotiated protocol version, or `None` when disconnected.

        Set during connect-time negotiation regardless of era: the initialize
        handshake, `server/discover`, or a direct version pin all populate it.
        """
        session = self._session_state.session
        return session.protocol_version if session is not None else None

    @property
    def server_capabilities(self) -> mcp_types.ServerCapabilities | None:
        """The server's advertised capabilities, or `None` when disconnected.

        Populated from whichever negotiation result the era produced (the
        `InitializeResult` on legacy, the `DiscoverResult` on modern).
        """
        session = self._session_state.session
        return session.server_capabilities if session is not None else None

    @property
    def server_info(self) -> mcp_types.Implementation | None:
        """The session's server identity, or `None` when disconnected.

        Populated from whichever negotiation result the era produced (the
        `InitializeResult` on legacy, the `DiscoverResult` on modern). A directly
        pinned modern version uses a synthesized identity with an empty name.
        """
        session = self._session_state.session
        return session.server_info if session is not None else None

    @property
    def instructions(self) -> str | None:
        """The server's instructions, or `None` when absent or disconnected.

        Populated from whichever negotiation result the era produced (the
        `InitializeResult` on legacy, the `DiscoverResult` on modern).
        """
        session = self._session_state.session
        return session.instructions if session is not None else None

    def set_roots(self, roots: RootsList | RootsHandler) -> None:
        """Set the roots for the client. This does not automatically call `send_roots_list_changed`."""
        self._session_kwargs["list_roots_callback"] = create_roots_callback(roots)

    def set_sampling_callback(
        self,
        sampling_callback: SamplingHandler,
        sampling_capabilities: mcp_types.SamplingCapability | None = None,
    ) -> None:
        """Set the sampling callback for the client."""
        self._session_kwargs["sampling_callback"] = create_sampling_callback(
            sampling_callback
        )
        self._session_kwargs["sampling_capabilities"] = (
            sampling_capabilities
            if sampling_capabilities is not None
            else mcp_types.SamplingCapability()
        )

    def set_elicitation_callback(
        self, elicitation_callback: ElicitationHandler
    ) -> None:
        """Set the elicitation callback for the client."""
        self._elicitation_callback = create_elicitation_callback(elicitation_callback)
        self._session_kwargs["elicitation_callback"] = self._elicitation_callback
        # Rebuild internal extensions (e.g. the tasks extension) so a background
        # task's in-task input is answered through the newly-set handler, not the
        # one captured when the client was constructed.
        self._session_kwargs.update(self._build_extension_kwargs())

    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return self._session_state.session is not None

    def new(self) -> Client[ClientTransportT]:
        """Create a new client instance with the same configuration but fresh session state.

        This creates a new client with the same transport, handlers, and configuration,
        but with no active session. Useful for creating independent sessions that don't
        share state with the original client.

        Returns:
            A new Client instance with the same configuration but disconnected state.

        Example:
            ```python
            # Create a fresh client for each concurrent operation
            fresh_client = client.new()
            async with fresh_client:
                await fresh_client.call_tool("some_tool", {})
            ```
        """
        new_client = copy.copy(self)

        # Always reset session state so cloned clients start disconnected and do not
        # share lifecycle state with the original instance.
        new_client._session_state = ClientSessionState()
        new_client._transport_options = self._transport_options

        # Give the clone its own response cache so cached entries are not shared
        # across independent sessions, and rebuild the negotiated_version closure
        # to point at the clone's session state.
        new_client._response_cache = new_client._build_response_cache(self._cache_arg)

        # Create a fresh session kwargs dict so the clone doesn't share
        # the original's mutable dict; preserve any custom message handler the
        # user may have set, re-wrapping with the clone's own cache if one exists.
        new_client._session_kwargs = {**self._session_kwargs}  # type: ignore[typeddict-item]
        base_handler: MessageHandlerFnT | None = self._base_message_handler
        new_client._base_message_handler = base_handler
        if new_client._response_cache is not None:
            new_client._session_kwargs["message_handler"] = _evicting_message_handler(
                new_client._response_cache, base_handler
            )
        else:
            new_client._session_kwargs["message_handler"] = base_handler
        # Rebuild the extension-contributed kwargs (capability ad, result claims,
        # notification bindings) so user extensions compose on the clone.
        new_client._session_kwargs.update(new_client._build_extension_kwargs())

        new_client.name += f":{secrets.token_hex(2)}"

        return new_client

    @asynccontextmanager
    async def _context_manager(self):
        # Only passed when this client actually wants non-default settings, so an
        # ordinary client never sends an argument a transport might not accept.
        if self._transport_options is not None:
            connection = self.transport.connect_session(
                transport_options=self._transport_options, **self._session_kwargs
            )
        else:
            connection = self.transport.connect_session(**self._session_kwargs)

        with catch(get_catch_handlers()):
            async with connection as session:
                self._session_state.session = session
                # Initialize the session if auto_initialize is enabled
                try:
                    if self.auto_initialize:
                        await self._negotiate()
                    yield
                except anyio.ClosedResourceError as e:
                    raise RuntimeError("Server session was closed unexpectedly") from e
                finally:
                    self._reset_session_state()

    async def _negotiate(
        self,
        timeout: datetime.timedelta | float | int | None = None,
    ) -> None:
        """Run the connect-time protocol negotiation dictated by ``self.mode``.

        - ``"legacy"``: today's initialize handshake (populates ``initialize_result``).
        - ``"auto"``: probe ``server/discover`` at the newest modern version and adopt it,
          denylist-falling-back to the initialize handshake for handshake-era servers.
        - a modern version string: adopt that version directly (from ``prior_discover`` if
          supplied, else a synthesized minimal ``DiscoverResult``).

        Idempotent: once the session has a negotiated protocol version, this is a no-op.
        """
        if self.session.protocol_version is not None:
            # Already negotiated (e.g. a manual initialize() call before context entry).
            if self.session.initialize_result is not None:
                self._session_state.initialize_result = self.session.initialize_result
            return

        if timeout is None:
            timeout = self._init_timeout
        else:
            timeout = normalize_timeout_to_seconds(timeout)

        # A legacy-only transport (SSE, a multi-server proxy config) cannot serve
        # the modern era; treat "auto" as "legacy" there rather than probing
        # server/discover, which some such servers answer but then cannot serve.
        effective_mode = self.mode
        if effective_mode == "auto" and self.transport.legacy_only:
            effective_mode = "legacy"

        try:
            with anyio.fail_after(timeout):
                if effective_mode == "legacy":
                    self._session_state.initialize_result = (
                        await self.session.initialize()
                    )
                elif effective_mode == "auto":
                    async with _conformant_discover_only(self.session):
                        await negotiate_auto(self.session)
                    # auto may have fallen back to the legacy handshake; surface its
                    # InitializeResult through the existing public property when so.
                    self._session_state.initialize_result = (
                        self.session.initialize_result
                    )
                else:
                    self.session.adopt(
                        self._prior_discover or _synthesize_discover(self.mode)
                    )
        except TimeoutError as e:
            raise RuntimeError("Failed to initialize server session") from e

    async def initialize(
        self,
        timeout: datetime.timedelta | float | int | None = None,
    ) -> mcp_types.InitializeResult:
        """Send an initialize request to the server.

        This method performs the MCP initialization handshake with the server,
        exchanging capabilities and server information. It is idempotent - calling
        it multiple times returns the cached result from the first call.

        The initialization happens automatically when entering the client context
        manager unless `auto_initialize=False` was set during client construction.
        Manual calls to this method are only needed when auto-initialization is disabled.

        With `mode="auto"` or a pinned modern version, connect-time negotiation may adopt
        the modern `server/discover` era, which has no `InitializeResult`; in that case
        this method raises. Read `protocol_version`, `server_info`,
        `server_capabilities`, and `instructions` instead, or use `mode="legacy"`
        when you need the handshake result.

        Args:
            timeout: Optional timeout for the initialization request (seconds or timedelta).
                If None, uses the client's init_timeout setting.

        Returns:
            InitializeResult: The server's initialization response containing server info,
                capabilities, protocol version, and optional instructions.

        Raises:
            RuntimeError: If the client is not connected, initialization times out, or the
                negotiated era carries no `InitializeResult` (a modern `discover` connection).

        Example:
            ```python
            # With auto-initialization disabled
            client = Client(server, auto_initialize=False)
            async with client:
                result = await client.initialize()
                print(f"Server: {result.server_info.name}")
                print(f"Instructions: {result.instructions}")
            ```
        """

        if self.initialize_result is not None:
            return self.initialize_result

        await self._negotiate(timeout=timeout)

        if self.initialize_result is None:
            raise RuntimeError(
                "The client negotiated a modern protocol era (server/discover), which has "
                "no InitializeResult. Inspect client.protocol_version, client.server_info, "
                "client.server_capabilities, and client.instructions for the metadata "
                "available in this mode, or construct the client with mode='legacy'."
            )
        return self.initialize_result

    async def __aenter__(self):
        return await self._connect()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._disconnect()

    async def _connect(self):
        """
        Establish or reuse a session connection.

        This method implements the reentrant context manager pattern:
        - First call: Creates background session task and waits for it to be ready
        - Subsequent calls: Increments reference counter and reuses existing session
        - All operations protected by _context_lock to prevent race conditions

        The critical fix: Events are only created when starting a new session,
        never reset outside the lock, preventing the deadlock scenario where
        tasks wait on events that get replaced by other tasks.
        """
        # ensure only one session is running at a time to avoid race conditions
        async with self._session_state.lock:
            need_to_start = (
                self._session_state.session_task is None
                or self._session_state.session_task.done()
            )

            if need_to_start:
                if self._session_state.nesting_counter != 0:
                    raise RuntimeError(
                        f"Internal error: nesting counter should be 0 when starting new session, got {self._session_state.nesting_counter}"
                    )
                self._session_state.stop_event = anyio.Event()
                self._session_state.ready_event = anyio.Event()
                self._session_state.session_task = asyncio.create_task(
                    self._session_runner()
                )
                try:
                    await self._session_state.ready_event.wait()
                except asyncio.CancelledError:
                    # Cancellation during initial connection startup can leave the
                    # background session task running because __aexit__ is never invoked
                    # when __aenter__ is cancelled. Since we hold the session lock here
                    # and we know we started the session task, it's safe to tear it down
                    # without impacting other active contexts.
                    #
                    # Note: session_task is an asyncio.Task (not anyio) because it needs
                    # to outlive individual context manager scopes - anyio's structured
                    # concurrency doesn't allow tasks to escape their task group.
                    session_task = self._session_state.session_task
                    if session_task is not None:
                        # Request a graceful stop if the runner has already reached
                        # its stop_event wait.
                        self._session_state.stop_event.set()
                        session_task.cancel()
                        with anyio.CancelScope(shield=True):
                            with anyio.move_on_after(3):
                                try:
                                    await session_task
                                except asyncio.CancelledError:
                                    pass
                                except Exception as e:
                                    logger.debug(
                                        f"Error during cancelled session cleanup: {e}"
                                    )

                    # Reset session state so future callers can reconnect cleanly.
                    self._reset_session_state(full=True)

                    with anyio.CancelScope(shield=True):
                        with anyio.move_on_after(3):
                            try:
                                await self.transport.close()
                            except Exception as e:
                                logger.debug(
                                    f"Error closing transport after cancellation: {e}"
                                )

                    raise

                if self._session_state.session_task.done():
                    exception = self._session_state.session_task.exception()
                    if exception is None:
                        raise RuntimeError(
                            "Session task completed without exception but connection failed"
                        )
                    # Preserve specific exception types that clients may want to handle
                    if isinstance(exception, httpx2.HTTPStatusError | MCPError):
                        raise exception
                    raise RuntimeError(
                        f"Client failed to connect: {exception}"
                    ) from exception

            self._session_state.nesting_counter += 1

        return self

    async def _disconnect(self, force: bool = False):
        """
        Disconnect from session using reference counting.

        This method implements proper cleanup for reentrant context managers:
        - Decrements reference counter for normal exits
        - Only stops session when counter reaches 0 (no more active contexts)
        - Force flag bypasses reference counting for immediate shutdown
        - Session cleanup happens inside the lock to ensure atomicity

        Key fix: Removed the problematic "Reset for future reconnects" logic
        that was resetting events outside the lock, causing race conditions.
        Event recreation now happens only in _connect() when actually needed.
        """
        # ensure only one session is running at a time to avoid race conditions
        async with self._session_state.lock:
            # if we are forcing a disconnect, reset the nesting counter
            if force:
                self._session_state.nesting_counter = 0

            # otherwise decrement to check if we are done nesting
            else:
                self._session_state.nesting_counter = max(
                    0, self._session_state.nesting_counter - 1
                )

            # if we are still nested, return
            if self._session_state.nesting_counter > 0:
                return

            # stop the active session
            if self._session_state.session_task is None:
                return
            session_task = self._session_state.session_task
            self._session_state.stop_event.set()
            # Wait (bounded) for the runner to unwind gracefully. If it
            # overruns — e.g. the transport's termination POST is blocked on
            # a stale HTTP keep-alive connection — cancel the background
            # task so transport resources (httpx connections, subprocess
            # pipes) are actually released instead of leaking into the
            # event loop. Force paths additionally shield the wait so an
            # outer cancellation can't abandon cleanup half-done.
            try:
                with anyio.CancelScope(shield=force):
                    with anyio.move_on_after(self._disconnect_timeout):
                        with suppress(asyncio.CancelledError):
                            await session_task
            finally:
                if not session_task.done():
                    session_task.cancel()
                    with anyio.CancelScope(shield=True):
                        with anyio.move_on_after(self._disconnect_timeout):
                            with suppress(Exception):
                                await session_task
                self._session_state.session_task = None

    async def _session_runner(self):
        """
        Background task that manages the actual session lifecycle.

        This task runs in the background and:
        1. Establishes the transport connection via _context_manager()
        2. Signals that the session is ready via _ready_event.set()
        3. Waits for disconnect signal via _stop_event.wait()
        4. Ensures _ready_event is always set, even on failures

        The simplified error handling (compared to the original) removes
        redundant exception re-raising while ensuring waiting tasks are
        always unblocked via the finally block.
        """
        try:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(self._context_manager())
                # Session/context is now ready
                self._session_state.ready_event.set()
                # Wait until disconnect/stop is requested
                await self._session_state.stop_event.wait()
        finally:
            # Ensure ready event is set even if context manager entry fails
            self._session_state.ready_event.set()

    async def _await_with_session_monitoring(
        self, coro: Coroutine[Any, Any, ResultT]
    ) -> ResultT:
        """Await a coroutine while monitoring the session task for errors.

        When using HTTP transports, server errors (4xx/5xx) are raised in the
        background session task, not in the coroutine waiting for a response.
        This causes the client to hang indefinitely since the response never
        arrives. This method monitors the session task and propagates any
        exceptions that occur, preventing the client from hanging.

        Args:
            coro: The coroutine to await (typically a session method call)

        Returns:
            The result of the coroutine

        Raises:
            The exception from the session task if it fails, or RuntimeError
            if the session task completes unexpectedly without an exception.
        """
        session_task = self._session_state.session_task

        # If no session task, just await directly
        if session_task is None:
            return await coro

        # If session task already failed, raise immediately
        if session_task.done():
            # Close the coroutine to avoid "was never awaited" warning
            coro.close()
            exc = session_task.exception()
            if exc:
                raise exc
            raise RuntimeError("Session task completed unexpectedly")

        # Create task for our call
        call_task = asyncio.create_task(coro)

        try:
            done, _ = await asyncio.wait(
                {call_task, session_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if session_task in done:
                # Session task completed (likely errored) before our call finished
                call_task.cancel()
                with anyio.CancelScope(shield=True), suppress(asyncio.CancelledError):
                    await call_task

                # Raise the session task exception
                exc = session_task.exception()
                if exc:
                    raise exc
                raise RuntimeError("Session task completed unexpectedly")

            # Our call completed first - get the result
            return call_task.result()
        except asyncio.CancelledError:
            call_task.cancel()
            with anyio.CancelScope(shield=True), suppress(asyncio.CancelledError):
                await call_task
            raise

    async def _cached_fetch(
        self,
        method: str,
        *,
        cursor: str | None,
        cache_mode: CacheMode,
        send: Callable[[], Coroutine[Any, Any, CacheableT]],
        absorb: Callable[[CacheableT], CacheableT] | None = None,
    ) -> CacheableT:
        """Serve one of the cacheable list verbs through the response cache.

        Mirrors the SDK Client's `_cached_fetch`: cursorless `use` calls are served
        from (and stored to) the cache; a cursor page skips the cache (and evicts on
        an expired-cursor `INVALID_PARAMS`, which signals the listing changed). The
        cache is inert on legacy connections because the SDK cache reads no TTL/scope
        hints below the modern era, so nothing is ever stored to serve.

        `absorb` (tools/list only) re-applies the session's derived per-tool state to a
        served cache hit, since a hit skips `session.list_tools`.
        """
        cache = self._response_cache
        if cache is None or cache_mode == "bypass":
            return await send()
        # A closed (or never-entered) client must raise, never serve cached entries.
        _ = self.session
        if cursor is not None:
            # Continuation pages skip the cache; an expired cursor means the listing
            # changed, so evict (spec SHOULD) and re-raise.
            try:
                return await send()
            except MCPError as e:
                if e.code == mcp_types.INVALID_PARAMS:
                    await cache.evict_method(method)
                raise
        if cache_mode == "use" and (hit := await cache.read(method, "")) is not None:
            served = cast(CacheableT, hit)
            return served if absorb is None else absorb(served)
        gen = cache.capture(method, "")
        result = await send()
        await cache.write(method, "", result, gen, cache_mode)
        return result

    async def _drive_input_required(
        self,
        first: ResultT | mcp_types.InputRequiredResult,
        retry: Callable[
            [mcp_types.InputResponses | None, str | None],
            Coroutine[Any, Any, ResultT | mcp_types.InputRequiredResult],
        ],
    ) -> ResultT:
        """Resolve a SEP-2322 `InputRequiredResult` to its terminal result.

        A 2026-era server may answer `tools/call` / `prompts/get` / `resources/read`
        with an `InputRequiredResult` carrying embedded sampling / elicitation / roots
        requests. This hands that off to the SDK's `run_input_required_driver`, which
        dispatches each embedded request through the *same* callback table that serves
        legacy server-initiated RPCs (`session.dispatch_input_request`) and retries the
        original call until it returns a terminal result.

        Legacy servers never emit `InputRequiredResult`, so a terminal `first` passes
        straight through untouched — zero behavior change for pre-2026 servers.
        """
        if not isinstance(first, mcp_types.InputRequiredResult):
            return first
        session = self.session

        async def dispatch(
            key: str, req: mcp_types.InputRequest
        ) -> mcp_types.InputResponse | mcp_types.ErrorData:
            ctx = ClientRequestContext(
                session=session,
                request_id=key,
                meta=req.params.meta if req.params else None,
            )
            return await session.dispatch_input_request(ctx, req)

        return await run_input_required_driver(
            first,
            dispatch=dispatch,
            retry=retry,
            max_rounds=self.input_required_max_rounds,
        )

    def _build_extension_kwargs(self) -> SessionKwargs:
        """Session kwargs contributed by `extensions=` / `result_claims=`.

        Folds the user's `ClientExtension` instances into the capability ad, result
        claims, and notification bindings the SDK `ClientSession` consumes, then
        merges in any explicitly-passed `result_claims`.

        Also rebuilds `self._claim_by_model`, the model→claim index the resolution
        path uses to finish a claimed `tools/call` result, covering both the folded
        extension claims and the explicit `result_claims` extras.

        FastMCP-internal extensions (e.g. the tasks extension from `fastmcp-tasks`,
        registered via `register_internal_client_extension_factory`) are folded in
        automatically so an ordinary `Client` transparently drives a server's
        background tasks. They lead the fold order; a user extension declaring the
        same identifier wins, so the internal one is dropped rather than colliding.
        """
        user_extensions = list(self._extensions_arg or ())
        user_identifiers = {
            identifier
            for extension in user_extensions
            if (identifier := getattr(extension, "identifier", None)) is not None
        }
        internal_extensions = (
            [
                extension
                for extension in build_internal_client_extensions(
                    self._elicitation_callback
                )
                if extension.identifier not in user_identifiers
            ]
            if self._auto_internal_extensions
            else []
        )
        folded = _fold_extensions([*internal_extensions, *user_extensions])

        claims: dict[str, tuple[ResultClaim[Any], ...]] = dict(folded.claims or {})
        by_model: dict[type[mcp_types.Result], ResultClaim[Any]] = dict(folded.by_model)
        for identifier, extra in (self._result_claims_arg or {}).items():
            existing = claims.get(identifier, ())
            claims[identifier] = (*existing, *extra)
            for claim in extra:
                by_model[claim.model] = claim
        self._claim_by_model = by_model

        kwargs: SessionKwargs = {
            "notification_bindings": [*(folded.bindings or ())],
        }
        if folded.ad:
            kwargs["extensions"] = folded.ad
        if claims:
            kwargs["result_claims"] = claims
        return kwargs

    async def _resolve_claimed_result(
        self,
        name: str,
        result: mcp_types.Result,
        read_timeout_seconds: float | None,
    ) -> mcp_types.CallToolResult:
        """Finish a claimed `tools/call` result through its owning extension.

        A modern server may answer `tools/call` with a claimed extension shape
        (SEP-2133). The session parses it into the claim's model; this hands that
        model to the owning claim's resolver — which may send follow-up requests
        through the session — and returns the ordinary `CallToolResult` it
        produces. Mirrors the SDK Client's resolution path, including the
        output-schema revalidation the direct path performs.
        """
        claim = self._claim_by_model[type(result)]
        final = await claim.resolve(
            result,
            ClaimContext(
                session=self.session,
                tool_name=name,
                read_timeout_seconds=read_timeout_seconds,
            ),
        )
        if not final.is_error:
            await self.session.validate_tool_result(name, final)
        return final

    async def close(self):
        await self._disconnect(force=True)
        await self.transport.close()

    # --- MCP Client Methods ---

    async def ping(self) -> bool:
        """Send a ping request."""
        result = await self._await_with_session_monitoring(self.session.send_ping())
        return isinstance(result, mcp_types.EmptyResult)

    async def cancel(
        self,
        request_id: str | int,
        reason: str | None = None,
    ) -> None:
        """Send a cancellation notification for an in-progress request."""
        notification = mcp_types.CancelledNotification(
            method="notifications/cancelled",
            params=mcp_types.CancelledNotificationParams(
                request_id=request_id,
                reason=reason,
            ),
        )
        await self.session.send_notification(notification)

    async def progress(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        """Send a progress notification."""
        # Deprecated upstream in SDK v2 but deliberately kept per compat directive;
        # removed with the multi-round-trip follow-up.
        await self.session.send_progress_notification(  # ty: ignore[deprecated]
            progress_token, progress, total, message
        )

    async def set_logging_level(self, level: mcp_types.LoggingLevel) -> None:
        """Send a logging/setLevel request."""
        # Deprecated upstream in SDK v2 but deliberately kept per compat directive;
        # removed with the multi-round-trip follow-up.
        await self._await_with_session_monitoring(
            self.session.set_logging_level(level)  # ty: ignore[deprecated]
        )

    async def send_roots_list_changed(self) -> None:
        """Send a roots/list_changed notification."""
        # Deprecated upstream in SDK v2 but deliberately kept per compat directive;
        # removed with the multi-round-trip follow-up.
        await self.session.send_roots_list_changed()  # ty: ignore[deprecated]

    # --- Completion ---

    async def complete_mcp(
        self,
        ref: mcp_types.ResourceTemplateReference | mcp_types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp_types.CompleteResult:
        """Send a completion request and return the complete MCP protocol result.

        Args:
            ref (mcp_types.ResourceTemplateReference | mcp_types.PromptReference): The reference to complete.
            argument (dict[str, str]): Arguments to pass to the completion request.
            context_arguments (dict[str, Any] | None, optional): Optional context arguments to
                include with the completion request. Defaults to None.

        Returns:
            mcp_types.CompleteResult: The complete response object from the protocol,
                containing the completion and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        logger.debug(f"[{self.name}] called complete: {ref}")

        result = await self._await_with_session_monitoring(
            self.session.complete(
                ref=ref, argument=argument, context_arguments=context_arguments
            )
        )
        return result

    async def complete(
        self,
        ref: mcp_types.ResourceTemplateReference | mcp_types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp_types.Completion:
        """Send a completion request to the server.

        Args:
            ref (mcp_types.ResourceTemplateReference | mcp_types.PromptReference): The reference to complete.
            argument (dict[str, str]): Arguments to pass to the completion request.
            context_arguments (dict[str, Any] | None, optional): Optional context arguments to
                include with the completion request. Defaults to None.

        Returns:
            mcp_types.Completion: The completion object.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        result = await self.complete_mcp(
            ref=ref, argument=argument, context_arguments=context_arguments
        )
        return result.completion

    @classmethod
    def generate_name(cls, name: str | None = None) -> str:
        class_name = cls.__name__
        if name is None:
            return f"{class_name}-{secrets.token_hex(2)}"
        else:
            return f"{class_name}-{name}-{secrets.token_hex(2)}"
