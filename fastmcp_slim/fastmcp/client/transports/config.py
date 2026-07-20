import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from mcp import ClientSession
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
        composite = FastMCP[Any](
            name="MCPRouter", request_state_security=self._request_state_security
        )

        # The composite is only a router: every real backend is reached through
        # one of the mounted proxies below, so the era the connecting client
        # negotiates with the composite means nothing unless those backend legs
        # negotiate it too. `backend_mode` carries the connecting client's era
        # down to them, keeping the whole chain on one era end to end.
        backend_mode = (
            transport_options.backend_mode if transport_options is not None else None
        )

        async with contextlib.AsyncExitStack() as stack:
            # Close any previous transports from prior connections to avoid leaking
            for t in self._transports:
                await t.close()
            self._transports = []

            for name, server_config in self.config.mcpServers.items():
                try:
                    transport, _client, proxy = await self._create_proxy(
                        name, server_config, timeout, stack, backend_mode
                    )
                except Exception:  # Broad catch is intentional: failure modes
                    # are diverse (OSError, TimeoutError, RuntimeError, etc.)
                    # and the whole point is to skip any server that can't connect.
                    logger.warning(
                        "Failed to connect to MCP server %r, skipping",
                        name,
                        exc_info=True,
                    )
                    continue
                self._transports.append(transport)
                composite.mount(proxy, namespace=name if self.name_as_prefix else None)

            if not self._transports:
                raise ConnectionError("All MCP servers failed to connect")

            async with FastMCPTransport(mcp=composite).connect_session(
                transport_options=transport_options, **session_kwargs
            ) as session:
                yield session

    async def _create_proxy(
        self,
        name: str,
        config: MCPServerTypes,
        timeout: float | None,
        stack: contextlib.AsyncExitStack,
        backend_mode: str | None = None,
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

        # Handle transforming servers - call base class to_transport() for underlying transport
        if isinstance(config, TransformingStdioMCPServer):
            transport = StdioMCPServer.to_transport(config)
            tool_transforms = config.tools
            include_tags = config.include_tags
            exclude_tags = config.exclude_tags
        elif isinstance(config, TransformingRemoteMCPServer):
            transport = RemoteMCPServer.to_transport(config)
            tool_transforms = config.tools
            include_tags = config.include_tags
            exclude_tags = config.exclude_tags
        else:
            transport = config.to_transport()

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
