import pytest
from mcp import MCPError
from mcp_types import TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.utilities.tests import asgi_server


@pytest.mark.parametrize("limit", [None, 4 * 1024 * 1024])
async def test_large_sse_tool_and_resource_results(limit: int | None):
    server = FastMCP("large-results")
    payload = "x" * (1024 * 1024 + 1)

    @server.tool
    def large_result() -> str:
        return payload

    @server.resource("data://large")
    def large_resource() -> str:
        return payload

    async with asgi_server(server, json_response=False) as running:
        transport = StreamableHttpTransport(
            running.url,
            httpx_client_factory=running.http_client,
            max_sse_event_size=limit,
        )
        async with Client(transport, mode="legacy") as client:
            result = await client.call_tool("large_result")
            assert result.data == payload
            resource = await client.read_resource("data://large")
            assert isinstance(resource[0], TextResourceContents)
            assert resource[0].text == payload


async def test_default_sse_event_size_still_rejects_large_results():
    server = FastMCP("large-results")

    @server.tool
    def large_result() -> str:
        return "x" * (1024 * 1024 + 1)

    async with asgi_server(server, json_response=False) as running:
        async with Client(running.transport(), mode="legacy") as client:
            with pytest.raises(MCPError, match="exceeded"):
                await client.call_tool("large_result")
