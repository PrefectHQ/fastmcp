"""Test that verifies the timeout fix for issue #2842 and #2845."""

import asyncio

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.utilities.tests import run_server_async


def create_test_server() -> FastMCP:
    """Create a FastMCP server with a slow tool."""
    server = FastMCP("TestServer")

    @server.tool
    async def slow_tool(duration: int = 6) -> str:
        """A tool that takes some time to complete."""
        await asyncio.sleep(duration)
        return f"Completed in {duration} seconds"

    return server


@pytest.fixture
async def streamable_http_server():
    """Start a test server and return its URL."""
    server = create_test_server()
    async with run_server_async(server) as url:
        yield url


@pytest.mark.integration
@pytest.mark.timeout(15)
async def test_slow_tool_with_http_transport(streamable_http_server: str):
    """Test that tools taking >5 seconds work correctly with HTTP transport.

    This test verifies the fix for:
    - Issue #2842: Client can't get result after upgrading to 2.14.2
    - Issue #2845: Server doesn't return results when tool takes >5 seconds

    The root cause was that the httpx client was created without explicit
    timeout configuration, defaulting to httpx's 5-second timeout.
    """
    async with Client(
        transport=StreamableHttpTransport(streamable_http_server)
    ) as client:
        # This should NOT timeout since we fixed the default timeout
        result = await client.call_tool("slow_tool", {"duration": 6})
        assert result.data == "Completed in 6 seconds"
