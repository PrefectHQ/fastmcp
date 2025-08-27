from typing import Any

from fastmcp.client.transports import (
    ClientTransport,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from fastmcp.mcp_config import (
    MCPConfig,
    MCPServerTypes,
)
from fastmcp.server.proxy import FastMCPProxy, ProxyClient
from fastmcp.server.server import FastMCP


def mcp_config_to_servers_and_transports(
    config: MCPConfig,
) -> list[tuple[str, FastMCP[Any], ClientTransport]]:
    """A utility function to convert each entry of an MCP Config into a transport and server."""
    return [
        mcp_server_type_to_servers_and_transports(name, mcp_server)
        for name, mcp_server in config.mcpServers.items()
    ]


def mcp_server_type_to_servers_and_transports(
    name: str,
    mcp_server: MCPServerTypes,
) -> tuple[str, FastMCP[Any], ClientTransport]:
    """A utility function to convert each entry of an MCP Config into a transport and server."""

    from fastmcp.mcp_config import (
        TransformingRemoteMCPServer,
        TransformingStdioMCPServer,
    )

    server: FastMCP[Any]
    transport: ClientTransport

    client_name = ProxyClient.generate_name(f"MCP_{name}")
    server_name = FastMCPProxy.generate_name(f"MCP_{name}")

    if isinstance(mcp_server, TransformingRemoteMCPServer | TransformingStdioMCPServer):
        server, transport = mcp_server._to_server_and_underlying_transport(
            server_name=server_name,
            client_name=client_name,
        )
    else:
        transport = mcp_server.to_transport()
        client: ProxyClient[StreamableHttpTransport | SSETransport | StdioTransport] = (
            ProxyClient(transport=transport, name=client_name)
        )

        server = FastMCP.as_proxy(name=server_name, backend=client)

    return name, server, transport
