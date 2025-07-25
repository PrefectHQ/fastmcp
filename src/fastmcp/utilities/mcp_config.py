from typing import Any

from fastmcp.mcp_config import MCPConfig
from fastmcp.server.server import FastMCP


def composite_server_from_mcp_config(
    config: MCPConfig, name_as_prefix: bool = True
) -> FastMCP[None]:
    """A utility function to create a composite server from an MCPConfig."""
    composite_server = FastMCP[None]()

    mount_mcp_config_into_server(config, composite_server, name_as_prefix)

    return composite_server


def mount_mcp_config_into_server(
    config: MCPConfig,
    server: FastMCP[Any],
    name_as_prefix: bool = True,
) -> None:
    """A utility function to mount the servers from an MCPConfig into a FastMCP server."""
    for name, mcp_server in config.mcpServers.items():
        server.mount(
            prefix=name if name_as_prefix else None,
            server=FastMCP.as_proxy(backend=mcp_server.to_transport()),
        )
