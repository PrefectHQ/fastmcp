import asyncio
import json
import sys
from collections.abc import Generator

import pytest
import uvicorn
from mcp import McpError
from starlette.applications import Starlette
from starlette.routing import Mount

from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import run_server_in_process


def fastmcp_server():
    """Fixture that creates a FastMCP server with tools, resources, and prompts."""
    server = FastMCP("TestServer")

    # Add a tool
    @server.tool()
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    # Add a second tool
    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.tool()
    async def sleep(seconds: float) -> str:
        """Sleep for a given number of seconds."""
        await asyncio.sleep(seconds)
        return f"Slept for {seconds} seconds"

    # Add a resource
    @server.resource(uri="data://users")
    async def get_users():
        return ["Alice", "Bob", "Charlie"]

    # Add a resource template
    @server.resource(uri="data://user/{user_id}")
    async def get_user(user_id: str):
        return {"id": user_id, "name": f"User {user_id}", "active": True}

    @server.resource(uri="request://headers")
    async def get_headers() -> dict[str, str]:
        request = get_http_request()

        return dict(request.headers)

    # Add a prompt
    @server.prompt()
    def welcome(name: str) -> str:
        """Example greeting prompt."""
        return f"Welcome to FastMCP, {name}!"

    return server


def run_server(host: str, port: int, **kwargs) -> None:
    fastmcp_server().run(host=host, port=port, **kwargs)


def run_nested_server(host: str, port: int) -> None:
    mcp_app = fastmcp_server().http_app(path="/final/mcp")

    mount = Starlette(routes=[Mount("/nest-inner", app=mcp_app)])
    mount2 = Starlette(
        routes=[Mount("/nest-outer", app=mount)],
        lifespan=mcp_app.lifespan,
    )
    server = uvicorn.Server(
        config=uvicorn.Config(
            app=mount2,
            host=host,
            port=port,
            log_level="error",
            lifespan="on",
        )
    )
    server.run()


@pytest.fixture(scope="module")
def streamable_http_server() -> Generator[str, None, None]:
    with run_server_in_process(run_server, transport="streamable-http") as url:
        yield f"{url}/mcp"


async def test_ping(streamable_http_server: str):
    """Test pinging the server."""
    async with Client(
        transport=StreamableHttpTransport(streamable_http_server)
    ) as client:
        result = await client.ping()
        assert result is True


async def test_http_headers(streamable_http_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=StreamableHttpTransport(
            streamable_http_server, headers={"X-DEMO-HEADER": "ABC"}
        )
    ) as client:
        raw_result = await client.read_resource("request://headers")
        json_result = json.loads(raw_result[0].text)  # type: ignore[attr-defined]
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_nested_streamable_http_server_resolves_correctly():
    # tests patch for
    # https://github.com/modelcontextprotocol/python-sdk/pull/659

    with run_server_in_process(run_nested_server) as url:
        async with Client(
            transport=StreamableHttpTransport(f"{url}/nest-outer/nest-inner/final/mcp")
        ) as client:
            result = await client.ping()
            assert result is True


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Timeout tests are flaky on Windows. Timeouts *are* supported but the tests are unreliable.",
)
class TestTimeout:
    async def test_timeout(self, streamable_http_server: str):
        # note this transport behaves differently than others and raises
        # McpError from the *client* context
        with pytest.raises(McpError, match="Timed out"):
            async with Client(
                transport=StreamableHttpTransport(streamable_http_server),
                timeout=0.01,
            ) as client:
                await client.call_tool("sleep", {"seconds": 0.1})

    async def test_timeout_tool_call(self, streamable_http_server: str):
        async with Client(
            transport=StreamableHttpTransport(streamable_http_server),
        ) as client:
            with pytest.raises(McpError):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.01)

    async def test_timeout_tool_call_overrides_client_timeout(
        self, streamable_http_server: str
    ):
        async with Client(
            transport=StreamableHttpTransport(streamable_http_server),
            timeout=2,
        ) as client:
            with pytest.raises(McpError):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.01)

    async def test_timeout_client_timeout_overrides_tool_call_timeout_if_lower(
        self, streamable_http_server: str
    ):
        with pytest.raises(McpError):
            async with Client(
                transport=StreamableHttpTransport(streamable_http_server),
                timeout=0.01,
            ) as client:
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=2)
