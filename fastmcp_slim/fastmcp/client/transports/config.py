import contextlib
import copy
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from mcp import ClientSession
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from typing_extensions import Unpack

from fastmcp import _install_hints
from fastmcp.client.transports.base import (
    ClientTransport,
    SessionKwargs,
    TransportOptions,
)
from fastmcp.client.transports.memory import FastMCPTransport
from fastmcp.mcp_config import (
    MCPConfig,
    MCPServerTypes,
    RemoteMCPServer,
    StdioMCPServer,
    TransformingRemoteMCPServer,
    TransformingStdioMCPServer,
    _coerce_tool_transform_configs,
)
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from mcp.server.request_state import RequestStateSecurity

    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


class MCPConfigTransport(ClientTransport):
    """Transport for connecting to one or more MCP servers defined in an MCPConfig.

    This transport provides a unified interface to multiple MCP servers defined in an MCPConfig
    object or dictionary matching the MCPConfig schema. It supports two key scenarios:

    1. If the MCPConfig contains exactly one server, it creates a direct transport to that server.
    2. If the MCPConfig contains multiple servers, it creates a composite client by mounting
       all servers on a single FastMCP instance, with each server's name, by default, used as its mounting prefix.

    In the multiserver case, tools are accessible with the prefix pattern `{server_name}_{tool_name}`
    and resources with the pattern `protocol://{server_name}/path/to/resource`.

    This is particularly useful for creating clients that need to interact with multiple specialized
    MCP servers through a single interface, simplifying client code.

    Examples:
        ```python
        from fastmcp import Client

        # Create a config with multiple servers
        config = {
            "mcpServers": {
                "weather": {
                    "url": "https://weather-api.example.com/mcp",
                    "transport": "http"
                },
                "calendar": {
                    "url": "https://calendar-api.example.com/mcp",
                    "transport": "http"
                }
            }
        }

        # Create a client with the config
        client = Client(config)

        async with client:
            # Access tools with prefixes
            weather = await client.call_tool("weather_get_forecast", {"city": "London"})
            events = await client.call_tool("calendar_list_events", {"date": "2023-06-01"})

            # Access resources with prefixed URIs
            icons = await client.read_resource("weather://weather/icons/sunny")
        ```
    """

    def __init__(self, config: MCPConfig | dict, name_as_prefix: bool = True):
        if isinstance(config, dict):
            config = MCPConfig.from_dict(config)
        self.config = config
        self.name_as_prefix = name_as_prefix
        # Backend transports for the most recent connection opened by *this*
        # transport instance. They are connection-scoped, so a `Client.new()`
        # clone gets its own `_transports` list (see `copy_for_new_client`):
        # `close()` on one client must not tear down another's backends.
        self._transports: list[ClientTransport] = []
        # Backends this instance has successfully connected through. `close()`
        # only releases one of these; a never-connected or failed-connect
        # client has nothing to tear down. Participation is sticky so a client
        # can still release a keep-alive backend after its context exits.
        self._participated_backends: list[ClientTransport] = []
        # Live connection counts per backend transport, keyed by `id()` because
        # transports need not be hashable. `_backend_users` is shared by
        # shallow copies: single-server clones share the eagerly-built backend
        # (to preserve OAuth state), so close must see another clone's live
        # connection and leave that backend alone. `_backend_local_users`
        # counts only this instance's own connections, so teardown can still
        # release a backend when this instance is the sole user (e.g. a
        # cancelled connect calling close directly).
        self._backend_users: dict[int, int] = {}
        self._backend_local_users: dict[int, int] = {}
        self._request_state_security: RequestStateSecurity | None = None
        self._resolved_legacy_only = False
        # The transport a clone copied from, if any. Era resolution is
        # connection-scoped, but callers historically observed it through the
        # transport they held even when `Client.new()` did the connecting
        # (both shared one object before clones got per-instance state).
        # Mirroring keeps that observable behavior without sharing teardown
        # state. Chains of clones resolve to the most recent connection.
        self._era_source: MCPConfigTransport | None = None

        if not self.config.mcpServers:
            raise ValueError("No MCP servers defined in the config")

        # For single server, create transport eagerly so it can be inspected
        if len(self.config.mcpServers) == 1:
            self.transport = next(iter(self.config.mcpServers.values())).to_transport()
            self._transports.append(self.transport)
        else:
            # Sealing policy for the composite router built in `connect_session`.
            # It is held here, not on the router, because the router is rebuilt
            # on every connection while a guard tool's multi-round-trip spans
            # several of them (a proxy builds a fresh backend client per
            # request). A per-router key would seal `request_state` on one round
            # and reject its own token on the next. Only multi-server configs
            # mount a router, so single-server configs skip the import entirely
            # (it pulls in the SDK's server tier). Aliased so the local binding
            # does not shadow the type-checking-only name in the annotation
            # above.
            from mcp.server.request_state import (
                RequestStateSecurity as _RequestStateSecurity,
            )

            self._request_state_security = _RequestStateSecurity.ephemeral()

    def copy_for_new_client(self) -> "MCPConfigTransport":
        """Return a transport for a `Client.new()` clone.

        The clone shares configuration and the eagerly-built backend transport
        of a single-server config (notably an OAuth provider and its token
        store) but starts with no connection of its own. Connection-scoped
        state — `_transports`, `_participated_backends`, and
        `_resolved_legacy_only` — is per-instance; `_backend_users` stays
        shared so `close()` can see that another clone is still using the
        shared backend. A shallow copy preserves the concrete class, so a
        public `MCPConfigTransport` subclass keeps its behavior and any extra
        attributes.

        A consequence of sharing the single-server backend is that clients
        cloned from the same config may share its keep-alive session or
        subprocess. Client session state (handlers, caches, negotiation) is
        still independent; multi-server configs build separate backends per
        client.
        """
        clone = copy.copy(self)
        backend = getattr(self, "transport", None)
        clone._transports = [backend] if backend is not None else []
        clone._participated_backends = []
        clone._backend_local_users = {}
        clone._resolved_legacy_only = False
        # Keep the source's observable era in sync with the clone's
        # connections without sharing connection/ownership state.
        clone._era_source = self
        return clone

    def _set_resolved_legacy_only(self, value: bool) -> None:
        """Record an era resolution and mirror it onto the clone's source.

        `legacy_only` is read after connecting by callers that may hold the
        source transport while a `Client.new()` clone performs the connection
        (e.g. adapters that clone per operation). Publish the latest resolution
        up the copy chain, as the pre-clone shared-object behavior did.
        """
        transport: MCPConfigTransport | None = self
        while transport is not None:
            transport._resolved_legacy_only = value
            transport = transport._era_source

    def _start_using(self, transport: ClientTransport) -> None:
        """Record a successful connection through `transport`."""
        if not any(existing is transport for existing in self._participated_backends):
            self._participated_backends.append(transport)
        key = id(transport)
        self._backend_users[key] = self._backend_users.get(key, 0) + 1
        self._backend_local_users[key] = self._backend_local_users.get(key, 0) + 1

    def _stop_using(self, transport: ClientTransport) -> None:
        """Release this instance's connection hold on `transport`."""
        key = id(transport)
        remaining = self._backend_users.get(key, 0) - 1
        if remaining > 0:
            self._backend_users[key] = remaining
        else:
            self._backend_users.pop(key, None)
        local = self._backend_local_users.get(key, 0) - 1
        if local > 0:
            self._backend_local_users[key] = local
        else:
            self._backend_local_users.pop(key, None)

    @property
    def legacy_only(self) -> bool:  # ty:ignore[invalid-property-type-override]
        """Whether the connected composite resolved to the legacy protocol era.

        A single-server config delegates directly to the underlying transport
        (no proxy), so it inherits that transport's era capability. A
        multi-server config resolves this value while connecting its backends:
        all-modern backends leave the composite modern-capable, while any
        legacy backend moves every leg to the handshake era.
        """
        if len(self.config.mcpServers) == 1:
            return self.transport.legacy_only
        return self._resolved_legacy_only

    @contextlib.asynccontextmanager
    async def connect_session(
        self,
        *,
        transport_options: TransportOptions | None = None,
        **session_kwargs: Unpack[SessionKwargs],
    ) -> AsyncGenerator[ClientSession, None]:
        # Single server - delegate directly to pre-created transport
        if len(self.config.mcpServers) == 1:
            config = next(iter(self.config.mcpServers.values()))
            if config.timeout is not None:
                session_kwargs["read_timeout_seconds"] = config.timeout / 1000 or None
            async with self.transport.connect_session(
                transport_options=transport_options, **session_kwargs
            ) as session:
                # Mark participation only once the connection actually
                # succeeds. A failed or cancelled connect must leave `close()`
                # with nothing to release (the same transport may be shared
                # with a clone whose connection is still live).
                self._start_using(self.transport)
                try:
                    yield session
                finally:
                    self._stop_using(self.transport)
            return

        # Multiple servers - create composite with mounted proxies, connecting
        # each ProxyClient so its underlying transport session stays alive for
        # the duration of this context (fixes session persistence for
        # streamable-http backends — see #2790).
        try:
            from fastmcp.server.server import FastMCP
        except ImportError as exc:
            raise ImportError(
                _install_hints.full_package("MCP configs with multiple servers")
            ) from exc

        timeout = session_kwargs.get("read_timeout_seconds")
        requested_mode = (
            transport_options.backend_mode
            if transport_options is not None
            and transport_options.backend_mode is not None
            else "legacy"
        )

        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        try:
            prepared_transports = self._prepare_transports()
            composite, backend_versions = await self._build_composite(
                FastMCP,
                timeout,
                stack,
                requested_mode,
                prepared_transports=prepared_transports,
                transport_options=transport_options,
            )

            # `auto` is an aggregate negotiation: the composite can only expose
            # one era to its caller, so a single legacy backend makes legacy the
            # best mutual era. Reconnect the modern backends under that era too;
            # otherwise push- and result-based interactions would meet halfway
            # through the proxy chain and fail despite the frontend fallback.
            legacy_backends = [
                version not in MODERN_PROTOCOL_VERSIONS for version in backend_versions
            ]
            if (
                requested_mode == "auto"
                and any(legacy_backends)
                and not all(legacy_backends)
            ):
                await stack.aclose()
                stack = contextlib.AsyncExitStack()
                await stack.__aenter__()
                composite, _ = await self._build_composite(
                    FastMCP,
                    timeout,
                    stack,
                    "legacy",
                    transport_options=transport_options,
                )
                self._set_resolved_legacy_only(True)
            else:
                self._set_resolved_legacy_only(
                    requested_mode == "legacy" or all(legacy_backends)
                )

            async with FastMCPTransport(mcp=composite).connect_session(
                transport_options=transport_options, **session_kwargs
            ) as session:
                opened_backends = list(self._transports)
                for backend in opened_backends:
                    self._start_using(backend)
                try:
                    yield session
                finally:
                    for backend in opened_backends:
                        self._stop_using(backend)
        finally:
            await stack.aclose()
            self._set_resolved_legacy_only(False)

    async def _build_composite(
        self,
        fastmcp_type: type["FastMCP[Any]"],
        timeout: float | None,
        stack: contextlib.AsyncExitStack,
        backend_mode: str,
        prepared_transports: dict[str, ClientTransport] | None = None,
        transport_options: TransportOptions | None = None,
    ) -> tuple["FastMCP[Any]", list[str]]:
        """Connect configured backends and mount their proxies on one router."""
        composite = fastmcp_type(
            name="MCPRouter", request_state_security=self._request_state_security
        )
        # Backends from a previous connection were released by its exit stack;
        # start this connection with a clean ownership set.
        self._transports = []
        self._participated_backends = []
        backend_versions: list[str] = []
        proxies: dict[str, FastMCP[Any]] = {}

        if prepared_transports is None:
            prepared_transports = self._prepare_transports()

        backends = [
            (name, config, prepared_transports.get(name))
            for name, config in self.config.mcpServers.items()
        ]
        if backend_mode == "auto":
            # Probe transports with a known legacy-only constraint first. Once
            # one connects, every remaining backend can start directly in the
            # aggregate's required legacy era instead of being restarted after
            # an avoidable modern probe.
            backends.sort(
                key=lambda backend: (
                    not (backend[2] is not None and backend[2].legacy_only)
                )
            )

        current_mode = backend_mode

        for name, server_config, transport in backends:
            try:
                connected_transport, client, proxy = await self._create_proxy(
                    name,
                    server_config,
                    timeout,
                    stack,
                    current_mode,
                    transport=transport,
                    transport_options=transport_options,
                )
            except Exception:  # Broad catch is intentional: failure modes
                # are diverse (OSError, TimeoutError, RuntimeError, etc.) and
                # one unavailable server must not take down healthy siblings.
                logger.warning(
                    "Failed to connect to MCP server %r, skipping",
                    name,
                    exc_info=True,
                )
                continue
            self._transports.append(connected_transport)
            assert client.protocol_version is not None
            backend_versions.append(client.protocol_version)
            proxies[name] = proxy
            if (
                backend_mode == "auto"
                and client.protocol_version not in MODERN_PROTOCOL_VERSIONS
            ):
                current_mode = "legacy"

        if not self._transports:
            raise ConnectionError("All MCP servers failed to connect")

        # Connection order may prioritize known legacy-only transports, but
        # mounted component order remains the order declared by the user.
        for name in self.config.mcpServers:
            if proxy := proxies.get(name):
                composite.mount(proxy, namespace=name if self.name_as_prefix else None)

        return composite, backend_versions

    def _prepare_transports(self) -> dict[str, ClientTransport]:
        """Create backend transports before connecting so capabilities are inspectable."""
        transports: dict[str, ClientTransport] = {}
        for name, config in self.config.mcpServers.items():
            try:
                transports[name] = self._create_transport(config)
            except Exception:
                logger.debug(
                    "Failed to construct transport for MCP server %r",
                    name,
                    exc_info=True,
                )
        return transports

    @staticmethod
    def _create_transport(config: MCPServerTypes) -> ClientTransport:
        """Create the transport used by a multi-server proxy backend."""
        if isinstance(config, TransformingStdioMCPServer):
            return StdioMCPServer.to_transport(config)
        if isinstance(config, TransformingRemoteMCPServer):
            return RemoteMCPServer.to_transport(config)
        return config.to_transport()

    async def _create_proxy(
        self,
        name: str,
        config: MCPServerTypes,
        timeout: float | None,
        stack: contextlib.AsyncExitStack,
        backend_mode: str | None = None,
        transport: ClientTransport | None = None,
        transport_options: TransportOptions | None = None,
    ) -> tuple[ClientTransport, Any, "FastMCP[Any]"]:
        """Create underlying transport, proxy client, and proxy server for a single backend.

        The ProxyClient is connected via the AsyncExitStack *before* being
        passed to create_proxy so the factory sees it as connected and reuses
        the same session for all tool calls (instead of creating fresh copies).

        `backend_mode` is the connect mode the calling client wants this backend
        leg to negotiate; `None` leaves the client at its own default era.

        Returns a tuple of (transport, proxy_client, proxy_server).
        """
        # Import here to avoid circular dependency
        from fastmcp.server.providers.proxy import StatefulProxyClient
        from fastmcp.server.server import create_proxy

        tool_transforms = None
        include_tags = None
        exclude_tags = None

        if transport is None:
            transport = self._create_transport(config)

        # Handle transforming servers.
        if isinstance(
            config, (TransformingStdioMCPServer, TransformingRemoteMCPServer)
        ):
            tool_transforms = config.tools
            include_tags = config.include_tags
            exclude_tags = config.exclude_tags

        client_kwargs: dict[str, Any] = {}
        if backend_mode is not None:
            client_kwargs["mode"] = backend_mode
        if transport_options is not None:
            client_kwargs["forward_incoming_headers"] = (
                transport_options.forward_incoming_headers
            )
        if config.timeout is not None:
            timeout = config.timeout / 1000
        client = StatefulProxyClient(
            transport=transport, timeout=timeout, **client_kwargs
        )
        # Connect the client *before* create_proxy so _create_client_factory
        # detects it as connected and reuses it for all tool calls, preserving
        # the session ID across requests. StatefulProxyClient is used instead
        # of ProxyClient because its context-restoring handler wrappers prevent
        # stale ContextVars in the reused session's receive loop.
        #
        # StatefulProxyClient.__aexit__ is a no-op (by design, for the
        # new_stateful() use case), so we cannot rely on enter_async_context
        # alone to clean up.  Instead we connect manually and push an
        # explicit force-disconnect callback so the subprocess is terminated
        # when the AsyncExitStack unwinds.
        await client.__aenter__()
        # Callbacks run LIFO: transport.close() must run *after*
        # client._disconnect so push it first.
        stack.push_async_callback(transport.close)
        stack.push_async_callback(client._disconnect, force=True)
        # Create proxy without include_tags/exclude_tags - we'll add them after tool transforms
        proxy = create_proxy(
            client,
            name=f"Proxy-{name}",
        )
        # Add tool transforms FIRST - they may add/modify tags
        if tool_transforms:
            from fastmcp.server.transforms import ToolTransform

            proxy.add_transform(
                ToolTransform(_coerce_tool_transform_configs(tool_transforms))
            )
        # Then add enabled filters - they filter based on tags
        if include_tags:
            proxy.enable(tags=set(include_tags), only=True)
        if exclude_tags:
            proxy.disable(tags=set(exclude_tags))
        return transport, client, proxy

    async def close(self):
        # Release only backends this instance successfully connected through,
        # and only while no other client's live connection is using them.
        # Single-server configs share their eagerly-built backend across
        # `Client.new()` clones, so an unconditional close would tear down a
        # clone's live session; a clone that never connected (or whose connect
        # failed or was cancelled) has no participation and releases nothing.
        # Subtracting this instance's own holds keeps direct teardown (e.g.
        # from a cancelled connect) able to release its own backend.
        for transport in self._transports:
            if not any(owned is transport for owned in self._participated_backends):
                continue
            key = id(transport)
            if self._backend_users.get(key, 0) > self._backend_local_users.get(key, 0):
                continue
            await transport.close()

    def __repr__(self) -> str:
        return f"<MCPConfigTransport(config='{self.config}')>"
