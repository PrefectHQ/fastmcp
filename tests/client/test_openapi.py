import json
import sys
from collections.abc import Generator

import pytest
import uvicorn
from fastapi import FastAPI, Request
from mcp.types import TextContent, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.utilities.tests import run_server_in_process


def fastmcp_server_for_headers() -> FastMCP:
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
        app, httpx_client_kwargs={"headers": {"X-SERVER": "test-abc"}}
    )

    return mcp


class TestClientHeaders:
    def run_shttp_server(self, host: str, port: int) -> None:
        try:
            app = fastmcp_server_for_headers().http_app(transport="streamable-http")
            server = uvicorn.Server(
                config=uvicorn.Config(
                    app=app,
                    host=host,
                    port=port,
                    log_level="error",
                    lifespan="on",
                )
            )
            server.run()
        except Exception as e:
            print(f"Server error: {e}")
            sys.exit(1)
        sys.exit(0)

    def run_sse_server(self, host: str, port: int) -> None:
        try:
            app = fastmcp_server_for_headers().http_app(transport="sse")
            server = uvicorn.Server(
                config=uvicorn.Config(
                    app=app,
                    host=host,
                    port=port,
                    log_level="error",
                    lifespan="on",
                )
            )
            server.run()
        except Exception as e:
            print(f"Server error: {e}")
            sys.exit(1)
        sys.exit(0)

    def run_proxy_server(self, host: str, port: int, remote_url: str) -> None:
        try:
            client = Client(transport=StreamableHttpTransport(remote_url))
            app = FastMCP.as_proxy(client).http_app(transport="streamable-http")
            server = uvicorn.Server(
                config=uvicorn.Config(
                    app=app,
                    host=host,
                    port=port,
                    log_level="error",
                    lifespan="on",
                )
            )
            server.run()
        except Exception as e:
            print(f"Server error: {e}")
            sys.exit(1)
        sys.exit(0)

    @pytest.fixture(scope="class")
    def shttp_server(self) -> Generator[str, None, None]:
        with run_server_in_process(self.run_shttp_server) as url:
            yield f"{url}/mcp"

    @pytest.fixture(scope="class")
    def sse_server(self) -> Generator[str, None, None]:
        with run_server_in_process(self.run_sse_server) as url:
            yield f"{url}/sse"

    @pytest.fixture(scope="class")
    def proxy_server(self, shttp_server: str) -> Generator[str, None, None]:
        with run_server_in_process(self.run_proxy_server, shttp_server + "/mcp") as url:
            yield f"{url}/mcp"

    async def test_client_headers_sse_resource(self, sse_server: str):
        async with Client(
            transport=SSETransport(sse_server, headers={"X-TEST": "test-123"})
        ) as client:
            result = await client.read_resource("resource://get_headers_headers_get")
            assert isinstance(result[0], TextResourceContents)
            headers = json.loads(result[0].text)
            assert headers["x-test"] == "test-123"

    async def test_client_headers_shttp_resource(self, shttp_server: str):
        async with Client(
            transport=StreamableHttpTransport(
                shttp_server, headers={"X-TEST": "test-123"}
            )
        ) as client:
            result = await client.read_resource("resource://get_headers_headers_get")
            assert isinstance(result[0], TextResourceContents)
            headers = json.loads(result[0].text)
            assert headers["x-test"] == "test-123"

    async def test_client_headers_sse_resource_template(self, sse_server: str):
        async with Client(
            transport=SSETransport(sse_server, headers={"X-TEST": "test-123"})
        ) as client:
            result = await client.read_resource(
                "resource://get_header_by_name_headers/x-test"
            )
            assert isinstance(result[0], TextResourceContents)
            header = json.loads(result[0].text)
            assert header == "test-123"

    async def test_client_headers_shttp_resource_template(self, shttp_server: str):
        async with Client(
            transport=StreamableHttpTransport(
                shttp_server, headers={"X-TEST": "test-123"}
            )
        ) as client:
            result = await client.read_resource(
                "resource://get_header_by_name_headers/x-test"
            )
            assert isinstance(result[0], TextResourceContents)
            header = json.loads(result[0].text)
            assert header == "test-123"

    async def test_client_headers_sse_tool(self, sse_server: str):
        async with Client(
            transport=SSETransport(sse_server, headers={"X-TEST": "test-123"})
        ) as client:
            result = await client.call_tool("post_headers_headers_post")
            assert isinstance(result[0], TextContent)
            headers = json.loads(result[0].text)
            assert headers["x-test"] == "test-123"

    async def test_client_headers_shttp_tool(self, shttp_server: str):
        async with Client(
            transport=StreamableHttpTransport(
                shttp_server, headers={"X-TEST": "test-123"}
            )
        ) as client:
            result = await client.call_tool("post_headers_headers_post")
            assert isinstance(result[0], TextContent)
            headers = json.loads(result[0].text)
            assert headers["x-test"] == "test-123"

    async def test_client_overrides_server_headers(self, shttp_server: str):
        async with Client(
            transport=StreamableHttpTransport(
                shttp_server, headers={"X-SERVER": "test-client"}
            )
        ) as client:
            result = await client.read_resource("resource://get_headers_headers_get")
            assert isinstance(result[0], TextResourceContents)
            headers = json.loads(result[0].text)
            assert headers["x-server"] == "test-client"

    async def test_client_headers_proxy(self, proxy_server: str):
        """
        Test that client headers are passed through the proxy to the remove server.
        """
        async with Client(transport=StreamableHttpTransport(proxy_server)) as client:
            result = await client.read_resource("resource://get_headers_headers_get")
            assert isinstance(result[0], TextResourceContents)
            headers = json.loads(result[0].text)
            assert headers["x-server"] == "test-abc"
