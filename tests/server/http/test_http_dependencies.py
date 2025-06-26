import json
from collections.abc import Generator

import pytest

from fastmcp.client import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import run_server_in_process


def fastmcp_server():
    server = FastMCP()

    # Add a tool
    @server.tool
    def get_headers_tool() -> dict[str, str]:
        """Get the HTTP headers from the request."""
        request = get_http_request()

        return dict(request.headers)

    @server.resource(uri="request://headers")
    async def get_headers_resource() -> dict[str, str]:
        request = get_http_request()

        return dict(request.headers)

    # Add a prompt
    @server.prompt
    def get_headers_prompt() -> str:
        """Get the HTTP headers from the request."""
        request = get_http_request()

        return json.dumps(dict(request.headers))

    return server


def run_server(host: str, port: int, **kwargs) -> None:
    fastmcp_server().run(host=host, port=port, **kwargs)


@pytest.fixture(autouse=True, scope="module")
def shttp_server() -> Generator[str, None, None]:
    with run_server_in_process(run_server, transport="http") as url:
        yield f"{url}/mcp/"


@pytest.fixture(autouse=True, scope="module")
def sse_server() -> Generator[str, None, None]:
    with run_server_in_process(run_server, transport="sse") as url:
        yield f"{url}/sse/"


async def test_http_headers_resource_shttp(shttp_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=StreamableHttpTransport(
            shttp_server, headers={"X-DEMO-HEADER": "ABC"}
        )
    ) as client:
        raw_result = await client.read_resource("request://headers")
        json_result = json.loads(raw_result[0].text)  # type: ignore[attr-defined]
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_resource_sse(sse_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=SSETransport(sse_server, headers={"X-DEMO-HEADER": "ABC"})
    ) as client:
        raw_result = await client.read_resource("request://headers")
        json_result = json.loads(raw_result[0].text)  # type: ignore[attr-defined]
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
        json_result = json.loads(result[0].text)  # type: ignore[attr-defined]
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_tool_sse(sse_server: str):
    async with Client(
        transport=SSETransport(sse_server, headers={"X-DEMO-HEADER": "ABC"})
    ) as client:
        result = await client.call_tool("get_headers_tool")
        json_result = json.loads(result[0].text)  # type: ignore[attr-defined]
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_prompt_shttp(shttp_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=StreamableHttpTransport(
            shttp_server, headers={"X-DEMO-HEADER": "ABC"}
        )
    ) as client:
        result = await client.get_prompt("get_headers_prompt")
        json_result = json.loads(result.messages[0].content.text)  # type: ignore[attr-defined]
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_prompt_sse(sse_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=SSETransport(sse_server, headers={"X-DEMO-HEADER": "ABC"})
    ) as client:
        result = await client.get_prompt("get_headers_prompt")
        json_result = json.loads(result.messages[0].content.text)  # type: ignore[attr-defined]
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"
