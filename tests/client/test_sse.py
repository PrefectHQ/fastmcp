import asyncio
import json
import sys

import pytest
from mcp import McpError
from mcp.types import TextResourceContents

from fastmcp.client import Client
from fastmcp.client.transports import SSETransport
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.http import create_sse_app
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import run_server_async


def create_test_server() -> FastMCP:
    """Create a FastMCP server with tools, resources, and prompts."""
    server = FastMCP("TestServer")

    @server.tool
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.tool
    async def sleep(seconds: float) -> str:
        """Sleep for a given number of seconds."""
        await asyncio.sleep(seconds)
        return f"Slept for {seconds} seconds"

    @server.resource(uri="data://users")
    async def get_users() -> str:
        import json

        return json.dumps(["Alice", "Bob", "Charlie"])

    @server.resource(uri="data://user/{user_id}")
    async def get_user(user_id: str) -> str:
        import json

        return json.dumps({"id": user_id, "name": f"User {user_id}", "active": True})

    @server.resource(uri="request://headers")
    async def get_headers() -> str:
        import json

        request = get_http_request()
        return json.dumps(dict(request.headers))

    @server.prompt
    def welcome(name: str) -> str:
        """Example greeting prompt."""
        return f"Welcome to FastMCP, {name}!"

    return server


@pytest.fixture
async def sse_server():
    """Start a test server with SSE transport and return its URL."""
    server = create_test_server()
    async with run_server_async(server, transport="sse") as url:
        yield url


async def test_ping(sse_server: str):
    """Test pinging the server."""
    async with Client(transport=SSETransport(sse_server)) as client:
        result = await client.ping()
        assert result is True


async def test_http_headers(sse_server: str):
    """Test getting HTTP headers from the server."""
    async with Client(
        transport=SSETransport(sse_server, headers={"X-DEMO-HEADER": "ABC"})
    ) as client:
        raw_result = await client.read_resource("request://headers")
        assert isinstance(raw_result[0], TextResourceContents)
        json_result = json.loads(raw_result[0].text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


@pytest.fixture
async def sse_server_custom_path():
    """Start a test server with SSE on a custom path."""
    server = create_test_server()
    async with run_server_async(server, transport="sse", path="/help") as url:
        yield url


@pytest.fixture
async def nested_sse_server():
    """Test nested server mounts with SSE."""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from fastmcp.utilities.http import find_available_port

    server = create_test_server()
    sse_app = create_sse_app(
        server=server, message_path="/mcp/messages", sse_path="/mcp/sse/"
    )

    # Nest the app under multiple mounts to test URL resolution
    inner = Starlette(routes=[Mount("/nest-inner", app=sse_app)])
    outer = Starlette(routes=[Mount("/nest-outer", app=inner)])

    # Run uvicorn with the nested ASGI app
    port = find_available_port()

    config = uvicorn.Config(
        app=outer,
        host="127.0.0.1",
        port=port,
        log_level="critical",
        ws="websockets-sansio",
    )

    uvicorn_server = uvicorn.Server(config)
    server_task = asyncio.create_task(uvicorn_server.serve())
    await asyncio.sleep(0.1)

    try:
        yield f"http://127.0.0.1:{port}/nest-outer/nest-inner/mcp/sse/"
    finally:
        # Graceful shutdown - required for uvicorn 0.39+ due to context isolation
        uvicorn_server.should_exit = True
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def test_run_server_on_path(sse_server_custom_path: str):
    """Test running server on a custom path."""
    async with Client(transport=SSETransport(sse_server_custom_path)) as client:
        result = await client.ping()
        assert result is True


async def test_nested_sse_server_resolves_correctly(nested_sse_server: str):
    """Test patch for https://github.com/modelcontextprotocol/python-sdk/pull/659"""
    async with Client(transport=SSETransport(nested_sse_server)) as client:
        result = await client.ping()
        assert result is True


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Timeout tests are flaky on Windows. Timeouts *are* supported but the tests are unreliable.",
)
class TestTimeout:
    async def test_timeout(self, sse_server: str):
        with pytest.raises(
            McpError,
            match="Timed out while waiting for response to ClientRequest. Waited 0.03 seconds",
        ):
            async with Client(
                transport=SSETransport(sse_server),
                timeout=0.03,
            ) as client:
                await client.call_tool("sleep", {"seconds": 0.1})

    async def test_timeout_tool_call(self, sse_server: str):
        async with Client(transport=SSETransport(sse_server)) as client:
            with pytest.raises(McpError, match="Timed out"):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.03)

    async def test_timeout_tool_call_overrides_client_timeout_if_lower(
        self, sse_server: str
    ):
        async with Client(
            transport=SSETransport(sse_server),
            timeout=2,
        ) as client:
            with pytest.raises(McpError, match="Timed out"):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.03)

    async def test_timeout_client_timeout_does_not_override_tool_call_timeout_if_lower(
        self, sse_server: str
    ):
        """
        With SSE, the tool call timeout always takes precedence over the client.

        Note: on Windows, the behavior appears unpredictable.
        """
        async with Client(
            transport=SSETransport(sse_server),
            timeout=0.1,
        ) as client:
            await client.call_tool("sleep", {"seconds": 0.03}, timeout=2)
