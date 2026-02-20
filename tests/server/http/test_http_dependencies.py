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


async def test_get_http_headers_excludes_content_type(sse_server: str):
    """Test that get_http_headers() excludes content-type header (issue #3097).

    This prevents HTTP 415 errors when forwarding headers to downstream APIs
    that require specific Content-Type headers (e.g., application/vnd.api+json).
    """
    from fastmcp.server.dependencies import get_http_headers

    server = FastMCP()

    @server.tool
    def check_excluded_headers() -> dict[str, str]:
        """Check that problematic headers are excluded from get_http_headers()."""
        return get_http_headers()

    async with run_server_async(server, transport="sse") as url:
        async with Client(
            transport=SSETransport(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Custom-Header": "should-be-included",
                },
            )
        ) as client:
            result = await client.call_tool("check_excluded_headers")
            headers = result.data

            # These headers should be excluded
            assert "content-type" not in headers
            assert "accept" not in headers
            assert "host" not in headers
            assert "content-length" not in headers

            # Custom headers should be included
            assert "x-custom-header" in headers
            assert headers["x-custom-header"] == "should-be-included"
