import contextlib
import datetime
from collections.abc import AsyncIterator
from typing import Any

from mcp import ClientSession
from typing_extensions import Unpack

from fastmcp.client.transports.base import ClientTransport, SessionKwargs
from fastmcp.client.transports.memory import FastMCPTransport
from fastmcp.mcp_config import (
    MCPConfig,
    MCPServerTypes,
    RemoteMCPServer,
    StdioMCPServer,
    TransformingRemoteMCPServer,
    TransformingStdioMCPServer,
)
from fastmcp.server.server import FastMCP, create_proxy


class MCPConfigTransport(ClientTransport):
    """Transport for connecting to one or more MCP servers defined in an MCPConfig.

    This transport provides a unified interface to multiple MCP servers defined in an MCPConfig
    object or dictionary matching the MCPConfig schema. It supports two key scenarios:

    1. If the MCPConfig contains exactly one server, it creates a direct transport to that server.
    2. If the MCPConfig contains multiple servers, it creates a composite client by mounting
       all servers on a single FastMCP instance, with each server's name, by default, used as its mounting prefix.

    In the multi-server case, tools are accessible with the prefix pattern `{server_name}_{tool_name}`
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

        if not self.config.mcpServers:
            raise ValueError("No MCP servers defined in the config")

        # For single server, create transport eagerly so it can be inspected
        if len(self.config.mcpServers) == 1:
            self.transport = next(iter(self.config.mcpServers.values())).to_transport()
            self._transports.append(self.transport)

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        # Single server - delegate directly to pre-created transport
        if len(self.config.mcpServers) == 1:
            async with self.transport.connect_session(**session_kwargs) as session:
                yield session
            return

        # Multiple servers - create composite with mounted proxies
        # Close any previous transports from prior connections to avoid leaking
        for t in self._transports:
            await t.close()
        self._transports = []
        timeout = session_kwargs.get("read_timeout_seconds")
        composite = FastMCP[Any](name="MCPRouter")

        try:
            for name, server_config in self.config.mcpServers.items():
                transport, proxy = self._create_proxy(name, server_config, timeout)
                self._transports.append(transport)
                composite.mount(proxy, namespace=name if self.name_as_prefix else None)
        except Exception:
            # Clean up any transports created before the failure
            for t in self._transports:
                await t.close()
            self._transports = []
            raise

        async with FastMCPTransport(mcp=composite).connect_session(
            **session_kwargs
        ) as session:
            yield session

    def _create_proxy(
        self,
        name: str,
        config: MCPServerTypes,
        timeout: datetime.timedelta | None,
    ) -> tuple[ClientTransport, FastMCP[Any]]:
        """Create underlying transport and proxy server for a single backend."""
        # Import here to avoid circular dependency
        from fastmcp.server.providers.proxy import ProxyClient

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

        client = ProxyClient(transport=transport, timeout=timeout)
        # Create proxy without include_tags/exclude_tags - we'll add them after tool transforms
        proxy = create_proxy(
            client,
            name=f"Proxy-{name}",
        )
        # Add tool transforms FIRST - they may add/modify tags
        if tool_transforms:
            from fastmcp.server.transforms import ToolTransform

            proxy.add_transform(ToolTransform(tool_transforms))
        # Then add enabled filters - they filter based on tags
        if include_tags:
            proxy.enable(tags=set(include_tags), only=True)
        if exclude_tags:
            proxy.disable(tags=set(exclude_tags))
        return transport, proxy

    async def close(self):
        for transport in self._transports:
            await transport.close()

    def __repr__(self) -> str:
        return f"<MCPConfigTransport(config='{self.config}')>"
