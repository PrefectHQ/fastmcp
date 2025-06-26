import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import uvicorn
from mcp import McpError
from mcp.types import TextContent
from starlette.applications import Starlette
from starlette.routing import Mount

from fastmcp import Context
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import run_server_in_process


def fastmcp_server():
    """Fixture that creates a FastMCP server with tools, resources, and prompts."""
    server = FastMCP("TestServer")

    # Add a tool
    @server.tool
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    # Add a second tool
    @server.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.tool
    async def sleep(seconds: float) -> str:
        """Sleep for a given number of seconds."""
        await asyncio.sleep(seconds)
        return f"Slept for {seconds} seconds"

    @server.tool
    async def greet_with_progress(name: str, ctx: Context) -> str:
        """Report progress for a greeting."""
        await ctx.report_progress(0.5, 1.0, "Greeting in progress")
        return f"Hello, {name}!"

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
    @server.prompt
    def welcome(name: str) -> str:
        """Example greeting prompt."""
        return f"Welcome to FastMCP, {name}!"

    return server


def run_server(host: str, port: int, stateless_http: bool = False, **kwargs) -> None:
    server = fastmcp_server()
    server.settings.stateless_http = stateless_http
    server.run(host=host, port=port, **kwargs)


def run_nested_server(host: str, port: int) -> None:
    mcp_app = fastmcp_server().http_app(path="/final/mcp/")

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


@pytest.fixture()
async def streamable_http_server(
    stateless_http: bool = False,
) -> AsyncGenerator[str, None]:
    with run_server_in_process(
        run_server, stateless_http=stateless_http, transport="http"
    ) as url:
        async with Client(transport=StreamableHttpTransport(f"{url}/mcp/")) as client:
            assert await client.ping()
        yield f"{url}/mcp/"


@pytest.fixture()
async def streamable_http_server_with_streamable_http_alias() -> AsyncGenerator[
    str, None
]:
    """Test that the "streamable-http" transport alias works."""
    with run_server_in_process(run_server, transport="streamable-http") as url:
        async with Client(transport=StreamableHttpTransport(f"{url}/mcp/")) as client:
            assert await client.ping()
        yield f"{url}/mcp/"


async def test_ping(streamable_http_server: str):
    """Test pinging the server."""
    async with Client(
        transport=StreamableHttpTransport(streamable_http_server)
    ) as client:
        result = await client.ping()
        assert result is True


async def test_ping_with_streamable_http_alias(
    streamable_http_server_with_streamable_http_alias: str,
):
    """Test pinging the server."""
    async with Client(
        transport=StreamableHttpTransport(
            streamable_http_server_with_streamable_http_alias
        )
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


@pytest.mark.parametrize("streamable_http_server", [True, False], indirect=True)
async def test_greet_with_progress_tool(streamable_http_server: str):
    """Test calling the greet tool."""
    progress_handler = AsyncMock(return_value=None)

    async with Client(
        transport=StreamableHttpTransport(streamable_http_server),
        progress_handler=progress_handler,
    ) as client:
        result = await client.call_tool("greet_with_progress", {"name": "Alice"})

        assert isinstance(result, list)
        assert isinstance(result[0], TextContent)
        assert result[0].text == "Hello, Alice!"

        progress_handler.assert_called_once_with(0.5, 1.0, "Greeting in progress")


async def test_nested_streamable_http_server_resolves_correctly():
    # tests patch for
    # https://github.com/modelcontextprotocol/python-sdk/pull/659

    with run_server_in_process(run_nested_server) as url:
        async with Client(
            transport=StreamableHttpTransport(f"{url}/nest-outer/nest-inner/final/mcp/")
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
                timeout=0.1,
            ) as client:
                await client.call_tool("sleep", {"seconds": 0.2})

    async def test_timeout_tool_call(self, streamable_http_server: str):
        async with Client(
            transport=StreamableHttpTransport(streamable_http_server),
        ) as client:
            with pytest.raises(McpError):
                await client.call_tool("sleep", {"seconds": 0.2}, timeout=0.1)

    async def test_timeout_tool_call_overrides_client_timeout(
        self, streamable_http_server: str
    ):
        async with Client(
            transport=StreamableHttpTransport(streamable_http_server),
            timeout=2,
        ) as client:
            with pytest.raises(McpError):
                await client.call_tool("sleep", {"seconds": 0.2}, timeout=0.1)
