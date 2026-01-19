"""Client timeout tests."""

import sys

import pytest
from mcp import McpError

from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.server.server import FastMCP


class TestTimeout:
    async def test_timeout(self, fastmcp_server: FastMCP):
        async with Client(
            transport=FastMCPTransport(fastmcp_server), timeout=0.05
        ) as client:
            with pytest.raises(
                McpError,
                match="Timed out while waiting for response to ClientRequest. Waited 0.05 seconds",
            ):
                await client.call_tool("sleep", {"seconds": 0.1})

    async def test_timeout_tool_call(self, fastmcp_server: FastMCP):
        async with Client(transport=FastMCPTransport(fastmcp_server)) as client:
            with pytest.raises(McpError):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.01)

    async def test_timeout_tool_call_overrides_client_timeout(
        self, fastmcp_server: FastMCP
    ):
        async with Client(
            transport=FastMCPTransport(fastmcp_server),
            timeout=2,
        ) as client:
            with pytest.raises(McpError):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.01)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="This test is flaky on Windows. Sometimes the client timeout is respected and sometimes it is not.",
    )
    async def test_timeout_tool_call_overrides_client_timeout_even_if_lower(
        self, fastmcp_server: FastMCP
    ):
        async with Client(
            transport=FastMCPTransport(fastmcp_server),
            timeout=0.01,
        ) as client:
            await client.call_tool("sleep", {"seconds": 0.1}, timeout=2)
