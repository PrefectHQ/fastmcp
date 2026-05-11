import json

import pytest
from fastapi import FastAPI, Request
from mcp.types import TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.server.providers.openapi import MCPType, RouteMap
from fastmcp.utilities.tests import run_server_async


def create_fastmcp_server_for_headers() -> FastMCP:
    """Create a FastMCP server from FastAPI app with experimental parser."""
    app = FastAPI()

    @app.get("/headers")
    def get_headers(request: Request):
        return request.headers

    @app.get("/headers/{header_name}")
    def get_header_by_name(header_name: str, request: Request):
        return request.headers[header_name]

    @app.post("/headers")
    def post_headers(request: Request):
        return request.headers

    mcp = FastMCP.from_fastapi(
        app,
        httpx_client_kwargs={"headers": {"x-server-header": "test-abc"}},
        route_maps=[
            # GET requests with path parameters go to ResourceTemplate
            RouteMap(
                methods=["GET"],
                pattern=r".*\{.*\}.*",
                mcp_type=MCPType.RESOURCE_TEMPLATE,
            ),
            # GET requests without path parameters go to Resource
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
        ],
    )

    return mcp


@pytest.fixture
async def shttp_server():
    """Start a test server with StreamableHttp transport."""
    server = create_fastmcp_server_for_headers()
    async with run_server_async(server, transport="http") as url:
        yield url


@pytest.fixture
async def sse_server():
    """Start a test server with SSE transport."""
    server = create_fastmcp_server_for_headers()
    async with run_server_async(server, transport="sse") as url:
        yield url


@pytest.fixture
async def proxy_server(shttp_server: str):
    """Start a proxy server."""
    proxy = FastMCP.as_proxy(StreamableHttpTransport(shttp_server))
    async with run_server_async(proxy, transport="http") as url:
        yield url


async def test_fastapi_client_headers_streamable_http_resource(shttp_server: str):
    async with Client(transport=StreamableHttpTransport(shttp_server)) as client:
        result = await client.read_resource("resource://get_headers_headers_get")
        assert isinstance(result[0], TextResourceContents)
        headers = json.loads(result[0].text)
        assert headers["x-server-header"] == "test-abc"


async def test_fastapi_client_headers_sse_resource(sse_server: str):
    async with Client(transport=SSETransport(sse_server)) as client:
        result = await client.read_resource("resource://get_headers_headers_get")
        assert isinstance(result[0], TextResourceContents)
        headers = json.loads(result[0].text)
        assert headers["x-server-header"] == "test-abc"


async def test_fastapi_client_headers_streamable_http_tool(shttp_server: str):
    async with Client(transport=StreamableHttpTransport(shttp_server)) as client:
        result = await client.call_tool("post_headers_headers_post")
        headers: dict[str, str] = result.data
        assert headers["x-server-header"] == "test-abc"


async def test_fastapi_client_headers_sse_tool(sse_server: str):
    async with Client(transport=SSETransport(sse_server)) as client:
        result = await client.call_tool("post_headers_headers_post")
        headers: dict[str, str] = result.data
        assert headers["x-server-header"] == "test-abc"


async def test_client_headers_sse_resource(sse_server: str):
    async with Client(
        transport=SSETransport(sse_server, headers={"X-TEST": "test-123"})
    ) as client:
        result = await client.read_resource("resource://get_headers_headers_get")
        assert isinstance(result[0], TextResourceContents)
        headers = json.loads(result[0].text)
        assert headers["x-test"] == "test-123"


async def test_client_headers_shttp_resource(shttp_server: str):
    async with Client(
        transport=StreamableHttpTransport(shttp_server, headers={"X-TEST": "test-123"})
    ) as client:
        result = await client.read_resource("resource://get_headers_headers_get")
        assert isinstance(result[0], TextResourceContents)
        headers = json.loads(result[0].text)
        assert headers["x-test"] == "test-123"


async def test_client_headers_sse_resource_template(sse_server: str):
    async with Client(
        transport=SSETransport(sse_server, headers={"X-TEST": "test-123"})
    ) as client:
        result = await client.read_resource(
            "resource://get_header_by_name_headers/x-test"
        )
        assert isinstance(result[0], TextResourceContents)
        header = json.loads(result[0].text)
        assert header == "test-123"


async def test_client_headers_shttp_resource_template(shttp_server: str):
    async with Client(
        transport=StreamableHttpTransport(shttp_server, headers={"X-TEST": "test-123"})
    ) as client:
        result = await client.read_resource(
            "resource://get_header_by_name_headers/x-test"
        )
        assert isinstance(result[0], TextResourceContents)
        header = json.loads(result[0].text)
        assert header == "test-123"


async def test_client_headers_sse_tool(sse_server: str):
    async with Client(
        transport=SSETransport(sse_server, headers={"X-TEST": "test-123"})
    ) as client:
        result = await client.call_tool("post_headers_headers_post")
        headers: dict[str, str] = result.data
        assert headers["x-test"] == "test-123"


async def test_client_headers_shttp_tool(shttp_server: str):
    async with Client(
        transport=StreamableHttpTransport(shttp_server, headers={"X-TEST": "test-123"})
    ) as client:
        result = await client.call_tool("post_headers_headers_post")
        headers: dict[str, str] = result.data
        assert headers["x-test"] == "test-123"


async def test_client_overrides_server_headers(shttp_server: str):
    async with Client(
        transport=StreamableHttpTransport(
            shttp_server, headers={"x-server-header": "test-client"}
        )
    ) as client:
        result = await client.read_resource("resource://get_headers_headers_get")
        assert isinstance(result[0], TextResourceContents)
        headers = json.loads(result[0].text)
        assert headers["x-server-header"] == "test-client"


async def test_client_with_excluded_header_is_ignored(sse_server: str):
    async with Client(
        transport=SSETransport(
            sse_server,
            headers={
                "x-server-header": "test-client",
                "host": "1.2.3.4",
                "not-host": "1.2.3.4",
            },
        )
    ) as client:
        result = await client.read_resource("resource://get_headers_headers_get")
        assert isinstance(result[0], TextResourceContents)
        headers = json.loads(result[0].text)
        assert headers["not-host"] == "1.2.3.4"
        assert headers["host"] == "fastapi"


@pytest.mark.flaky(retries=2, delay=1)
async def test_client_headers_proxy(proxy_server: str):
    """
    Test that client headers are passed through the proxy to the remove server.
    """
    async with Client(transport=StreamableHttpTransport(proxy_server)) as client:
        result = await client.read_resource("resource://get_headers_headers_get")
        assert isinstance(result[0], TextResourceContents)
        headers = json.loads(result[0].text)
        assert headers["x-server-header"] == "test-abc"
