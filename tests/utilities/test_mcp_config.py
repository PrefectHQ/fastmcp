from fastmcp.client.transports import (
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from fastmcp.utilities.mcp_config import LocalMCPServer, MCPConfig, RemoteMCPServer


def test_parse_single_stdio_config():
    config = {
        "mcpServers": {
            "test_server": {
                "command": "echo",
                "args": ["hello"],
            }
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    transport = mcp_config.mcpServers["test_server"].to_transport()
    assert isinstance(transport, StdioTransport)
    assert transport.command == "echo"
    assert transport.args == ["hello"]


def test_parse_single_remote_config():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000",
            }
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    transport = mcp_config.mcpServers["test_server"].to_transport()
    assert isinstance(transport, StreamableHttpTransport)
    assert transport.url == "http://localhost:8000"


def test_parse_remote_config_with_transport():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000",
                "transport": "sse",
            }
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    transport = mcp_config.mcpServers["test_server"].to_transport()
    assert isinstance(transport, SSETransport)
    assert transport.url == "http://localhost:8000"


def test_parse_remote_config_with_url_inference():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000/sse",
            }
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    transport = mcp_config.mcpServers["test_server"].to_transport()
    assert isinstance(transport, SSETransport)
    assert transport.url == "http://localhost:8000/sse"


def test_parse_multiple_servers():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000/sse",
            },
            "test_server_2": {
                "command": "echo",
                "args": ["hello"],
                "env": {"TEST": "test"},
            },
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    assert len(mcp_config.mcpServers) == 2
    assert isinstance(mcp_config.mcpServers["test_server"], RemoteMCPServer)
    assert isinstance(mcp_config.mcpServers["test_server"].to_transport(), SSETransport)

    assert isinstance(mcp_config.mcpServers["test_server_2"], LocalMCPServer)
    assert isinstance(
        mcp_config.mcpServers["test_server_2"].to_transport(), StdioTransport
    )
    assert mcp_config.mcpServers["test_server_2"].command == "echo"
    assert mcp_config.mcpServers["test_server_2"].args == ["hello"]
    assert mcp_config.mcpServers["test_server_2"].env == {"TEST": "test"}
