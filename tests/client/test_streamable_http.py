import asyncio
import json
import sys
from contextlib import suppress
from unittest.mock import AsyncMock, call

import pytest
from mcp import McpError
from mcp.types import TextResourceContents

from fastmcp import Context
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.dependencies import get_http_request
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
    async def elicit(ctx: Context) -> str:
        """Elicit a response from the user."""
        result = await ctx.elicit("What is your name?", response_type=str)

        if result.action == "accept":
            return f"You said your name was: {result.data}!"  # ty: ignore[possibly-missing-attribute]
        else:
            return "No name provided"

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
        await ctx.report_progress(0.75, 1.0, "Almost there!")
        return f"Hello, {name}!"

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
async def streamable_http_server(request):
    """Start a test server and return its URL."""
    import fastmcp

    stateless_http = getattr(request, "param", False)
    if stateless_http:
        fastmcp.settings.stateless_http = True

    server = create_test_server()
    async with run_server_async(server) as url:
        yield url

    if stateless_http:
        fastmcp.settings.stateless_http = False


@pytest.fixture
async def streamable_http_server_with_streamable_http_alias():
    """Test that the "streamable-http" transport alias works."""
    server = create_test_server()
    async with run_server_async(server, transport="streamable-http") as url:
        yield url


@pytest.fixture
async def nested_server():
    """Test nested server mounts with Starlette."""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from fastmcp.utilities.http import find_available_port

    mcp_server = create_test_server()
    mcp_app = mcp_server.http_app(path="/final/mcp")

    # Nest the app under multiple mounts to test URL resolution
    inner = Starlette(routes=[Mount("/nest-inner", app=mcp_app)])
    outer = Starlette(
        routes=[Mount("/nest-outer", app=inner)], lifespan=mcp_app.lifespan
    )

    # Run uvicorn with the nested ASGI app
    port = find_available_port()

    config = uvicorn.Config(
        app=outer,
        host="127.0.0.1",
        port=port,
        log_level="critical",
        ws="websockets-sansio",
        timeout_graceful_shutdown=0,
    )

    uvicorn_server = uvicorn.Server(config)
    server_task = asyncio.create_task(uvicorn_server.serve())
    await asyncio.sleep(0.1)

    yield f"http://127.0.0.1:{port}/nest-outer/nest-inner/final/mcp"

    # Graceful shutdown - required for uvicorn 0.39+ due to context isolation
    uvicorn_server.should_exit = True
    with suppress(asyncio.CancelledError, asyncio.TimeoutError):
        await asyncio.wait_for(server_task, timeout=2.0)


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
        assert isinstance(raw_result[0], TextResourceContents)
        json_result = json.loads(raw_result[0].text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_session_id_callback(streamable_http_server: str):
    """Test getting mcp-session-id from the transport."""
    transport = StreamableHttpTransport(streamable_http_server)
    assert transport.get_session_id() is None
    async with Client(transport=transport):
        session_id = transport.get_session_id()
        assert session_id is not None


@pytest.mark.parametrize("streamable_http_server", [True, False], indirect=True)
async def test_greet_with_progress_tool(streamable_http_server: str):
    """Test calling the greet tool."""
    progress_handler = AsyncMock(return_value=None)

    async with Client(
        transport=StreamableHttpTransport(streamable_http_server),
        progress_handler=progress_handler,
    ) as client:
        result = await client.call_tool("greet_with_progress", {"name": "Alice"})
        assert result.data == "Hello, Alice!"

        progress_handler.assert_has_calls(
            [
                call(0.5, 1.0, "Greeting in progress"),
                call(0.75, 1.0, "Almost there!"),
            ]
        )


@pytest.mark.parametrize("streamable_http_server", [True, False], indirect=True)
async def test_elicitation_tool(streamable_http_server: str, request):
    """Test calling the elicitation tool in both stateless and stateful modes."""

    async def elicitation_handler(message, response_type, params, ctx):
        return {"value": "Alice"}

    stateless_http = request.node.callspec.params.get("streamable_http_server", False)
    if stateless_http:
        pytest.xfail("Elicitation is not supported in stateless HTTP mode")

    async with Client(
        transport=StreamableHttpTransport(streamable_http_server),
        elicitation_handler=elicitation_handler,
    ) as client:
        result = await client.call_tool("elicit")
        assert result.data == "You said your name was: Alice!"


async def test_nested_streamable_http_server_resolves_correctly(nested_server: str):
    """Test patch for https://github.com/modelcontextprotocol/python-sdk/pull/659"""
    async with Client(transport=StreamableHttpTransport(nested_server)) as client:
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
                timeout=0.02,
            ) as client:
                await client.call_tool("sleep", {"seconds": 0.05})

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
