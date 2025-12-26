import json

import pytest
from mcp.types import TextContent, TextResourceContents

from fastmcp.client import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import run_server_async


def fastmcp_server():
    server = FastMCP()

    # Add a tool
    @server.tool
    def get_headers_tool() -> dict[str, str]:
        """Get the HTTP headers from the request."""
        request = get_http_request()

        return dict(request.headers)

    @server.resource(uri="request://headers")
    async def get_headers_resource() -> str:
        import json

        request = get_http_request()
        return json.dumps(dict(request.headers))

    # Add a prompt
    @server.prompt
    def get_headers_prompt() -> str:
        """Get the HTTP headers from the request."""
        request = get_http_request()

        return json.dumps(dict(request.headers))

    return server


@pytest.fixture
async def shttp_server():
    """Start a test server with StreamableHttp transport."""
    server = fastmcp_server()
    async with run_server_async(server, transport="http") as url:
        yield url


@pytest.fixture
async def sse_server():
    """Start a test server with SSE transport."""
    server = fastmcp_server()
    async with run_server_async(server, transport="sse") as url:
        yield url


async def test_http_headers_resource_shttp(shttp_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=StreamableHttpTransport(
            shttp_server, headers={"X-DEMO-HEADER": "ABC"}
        )
    ) as client:
        raw_result = await client.read_resource("request://headers")
        assert isinstance(raw_result[0], TextResourceContents)
        json_result = json.loads(raw_result[0].text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_resource_sse(sse_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=SSETransport(sse_server, headers={"X-DEMO-HEADER": "ABC"})
    ) as client:
        raw_result = await client.read_resource("request://headers")
        assert isinstance(raw_result[0], TextResourceContents)
        json_result = json.loads(raw_result[0].text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_tool_shttp(shttp_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=StreamableHttpTransport(
            shttp_server, headers={"X-DEMO-HEADER": "ABC"}
        )
    ) as client:
        result = await client.call_tool("get_headers_tool")
        assert "x-demo-header" in result.data
        assert result.data["x-demo-header"] == "ABC"


async def test_http_headers_tool_sse(sse_server: str):
    async with Client(
        transport=SSETransport(sse_server, headers={"X-DEMO-HEADER": "ABC"})
    ) as client:
        result = await client.call_tool("get_headers_tool")
        assert "x-demo-header" in result.data
        assert result.data["x-demo-header"] == "ABC"


async def test_http_headers_prompt_shttp(shttp_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=StreamableHttpTransport(
            shttp_server, headers={"X-DEMO-HEADER": "ABC"}
        )
    ) as client:
        result = await client.get_prompt("get_headers_prompt")
        assert isinstance(result.messages[0].content, TextContent)
        json_result = json.loads(result.messages[0].content.text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_prompt_sse(sse_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=SSETransport(sse_server, headers={"X-DEMO-HEADER": "ABC"})
    ) as client:
        result = await client.get_prompt("get_headers_prompt")
        assert isinstance(result.messages[0].content, TextContent)
        json_result = json.loads(result.messages[0].content.text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"
