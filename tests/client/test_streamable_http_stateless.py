from collections.abc import AsyncGenerator

import pytest
from mcp.types import TextContent

from fastmcp import Context
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import run_server_in_process


def fastmcp_server():
    """Fixture that creates a FastMCP server with tools, resources, and prompts."""
    server = FastMCP("TestServer")
    server.settings.stateless_http = True

    @server.tool
    async def greet_with_progress(name: str, ctx: Context) -> str:
        """Report progress for a greeting."""
        await ctx.report_progress(0.5, 1.0, "Greeting in progress")
        return f"Hello, {name}!"

    return server


def run_server(host: str, port: int, **kwargs) -> None:
    fastmcp_server().run(host=host, port=port, **kwargs)


@pytest.fixture()
async def streamable_http_server() -> AsyncGenerator[str, None]:
    with run_server_in_process(run_server, transport="streamable-http") as url:
        async with Client(transport=StreamableHttpTransport(f"{url}/mcp")) as client:
            assert await client.ping()
        yield f"{url}/mcp"


PROGRESS_MESSAGES = []

async def progress_handler(
    progress: float, total: float | None, message: str | None
) -> None:
    PROGRESS_MESSAGES.append(dict(progress=progress, total=total, message=message))


async def test_greet_with_progress_tool(streamable_http_server: str):
    """Test calling the greet tool."""
    async with Client(
        transport=StreamableHttpTransport(streamable_http_server), progress_handler=progress_handler
    ) as client:
        result = await client.call_tool("greet_with_progress", {"name": "Alice"})

        assert isinstance(result, list)
        assert isinstance(result[0], TextContent)
        assert result[0].text == "Hello, Alice!"

        assert PROGRESS_MESSAGES == [
            dict(progress=0.5, total=1.0, message="Greeting in progress"),
        ]
