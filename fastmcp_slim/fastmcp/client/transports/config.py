import contextlib
from collections.abc import AsyncIterator
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
        self._transports: list[ClientTransport] = []
        self._request_state_security: RequestStateSecurity | None = None
        self._resolved_legacy_only = False

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

    @property
    def legacy_only(self) -> bool:
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
    ) -> AsyncIterator[ClientSession]:
        # Single server - delegate directly to pre-created transport
        if len(self.config.mcpServers) == 1:
            async with self.transport.connect_session(
                transport_options=transport_options, **session_kwargs
            ) as session:
                yield session
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

        # Close transports retained from a previous connection before replacing
        # them. The active connection's exit stack owns their normal cleanup.
        for transport in self._transports:
            await transport.close()
        self._transports = []

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
                    FastMCP, timeout, stack, "legacy"
                )
                self._resolved_legacy_only = True
            else:
                self._resolved_legacy_only = requested_mode == "legacy" or all(
                    legacy_backends
                )

            async with FastMCPTransport(mcp=composite).connect_session(
                transport_options=transport_options, **session_kwargs
            ) as session:
                yield session
        finally:
            await stack.aclose()
            self._resolved_legacy_only = False

    async def _build_composite(
        self,
        fastmcp_type: type["FastMCP[Any]"],
        timeout: float | None,
        stack: contextlib.AsyncExitStack,
        backend_mode: str,
        prepared_transports: dict[str, ClientTransport] | None = None,
    ) -> tuple["FastMCP[Any]", list[str]]:
        """Connect configured backends and mount their proxies on one router."""
        composite = fastmcp_type(
            name="MCPRouter", request_state_security=self._request_state_security
        )
        self._transports = []
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
        for transport in self._transports:
            await transport.close()

    def __repr__(self) -> str:
        return f"<MCPConfigTransport(config='{self.config}')>"
