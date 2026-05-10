from __future__ import annotations

import builtins

import pytest


def test_fastmcp_client_imports_without_full_fastmcp() -> None:
    from fastmcp_client import Client
    from fastmcp_client.client.transports import (
        ClientTransport,
        StdioTransport,
        StreamableHttpTransport,
    )
    from fastmcp_client.exceptions import ToolError
    from fastmcp_client.mcp_config import MCPConfig

    assert Client is not None
    assert ClientTransport is not None
    assert StdioTransport(command="uvx", args=["mcp-run-python", "stdio"])
    assert StreamableHttpTransport is not None
    assert ToolError is not None
    assert MCPConfig.from_dict(
        {"mcpServers": {"demo": {"command": "uvx", "args": ["demo"]}}}
    )


@pytest.mark.asyncio
async def test_multiserver_config_requires_full_fastmcp_for_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp_client.client.transports import MCPConfigTransport

    original_import = builtins.__import__

    def block_fastmcp_server(name: str, *args: object, **kwargs: object) -> object:
        if name == "fastmcp.server.server":
            raise ImportError("blocked full fastmcp import")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_fastmcp_server)

    transport = MCPConfigTransport(
        {
            "mcpServers": {
                "one": {"command": "uvx", "args": ["one"]},
                "two": {"command": "uvx", "args": ["two"]},
            }
        }
    )

    with pytest.raises(ImportError, match="multiple servers require the full `fastmcp`"):
        async with transport.connect_session():
            pass
