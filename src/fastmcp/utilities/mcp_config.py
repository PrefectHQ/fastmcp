from typing import Any

from fastmcp.client.transports import ClientTransport
from fastmcp.mcp_config import (
    MCPConfig,
    MCPServerTypes,
)
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

    if isinstance(mcp_server, TransformingRemoteMCPServer | TransformingStdioMCPServer):
        server, transport = mcp_server._to_server_and_underlying_transport()
    else:
        transport = mcp_server.to_transport()
        server = FastMCP.as_proxy(backend=transport)

    return name, server, transport
