"""Client timeout tests."""

import pytest
from mcp import MCPError

from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.server.server import FastMCP


class TestTimeout:
    async def test_timeout(self, fastmcp_server: FastMCP):
        async with Client(
            transport=FastMCPTransport(fastmcp_server), timeout=0.05
        ) as client:
            with pytest.raises(
                MCPError,
                match="timed out",
            ):
                await client.call_tool("sleep", {"seconds": 0.1})

    async def test_timeout_tool_call(self, fastmcp_server: FastMCP):
        async with Client(transport=FastMCPTransport(fastmcp_server)) as client:
            with pytest.raises(MCPError):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.01)

    async def test_timeout_tool_call_overrides_client_timeout(
        self, fastmcp_server: FastMCP
    ):
        async with Client(
            transport=FastMCPTransport(fastmcp_server),
            timeout=2,
        ) as client:
            with pytest.raises(MCPError):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.01)

    async def test_timeout_tool_call_overrides_client_timeout_even_if_lower(
        self, fastmcp_server: FastMCP
    ):
        async with Client(
            transport=FastMCPTransport(fastmcp_server),
            timeout=0.1,
        ) as client:
            await client.call_tool("sleep", {"seconds": 0.5}, timeout=2)
