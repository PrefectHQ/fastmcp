import asyncio

import pytest
from anyio import create_task_group
from mcp.types import LoggingLevel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.proxy import FastMCPProxy, StatefulProxyClient
from fastmcp.utilities.tests import find_available_port


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP("TestServer")

    states: dict[int, int] = {}

    @mcp.tool
    async def log(
        message: str, level: LoggingLevel, logger: str, context: Context
    ) -> None:
        await context.log(message=message, level=level, logger_name=logger)

    @mcp.tool
    async def stateful_put(value: int, context: Context) -> None:
        """put a value associated with the server session"""
        key = id(context.session)
        states[key] = value

    @mcp.tool
    async def stateful_get(context: Context) -> int:
        """get the value associated with the server session"""
        key = id(context.session)
        try:
            return states[key]
        except KeyError:
            raise ToolError("Value not found")

    return mcp


@pytest.fixture
async def stateful_proxy_server(fastmcp_server: FastMCP):
    client = StatefulProxyClient(transport=FastMCPTransport(fastmcp_server))
    return FastMCPProxy(client_factory=client.new_stateful)


@pytest.fixture
async def stateless_server(stateful_proxy_server: FastMCP):
    port = find_available_port()
    url = f"http://127.0.0.1:{port}/mcp/"

    task = asyncio.create_task(
        stateful_proxy_server.run_http_async(
            host="127.0.0.1", port=port, stateless_http=True
        )
    )
    await stateful_proxy_server._started.wait()
    yield url
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestStatefulProxyClient:
    async def test_concurrent_log_requests_no_mixing(
        self, stateful_proxy_server: FastMCP
    ):
        """Test that concurrent log requests don't mix handlers (fixes #1068)."""
        results: dict[str, LogMessage] = {}

        async def log_handler_a(message: LogMessage) -> None:
            results["logger_a"] = message

        async def log_handler_b(message: LogMessage) -> None:
            results["logger_b"] = message

        async with (
            Client(stateful_proxy_server, log_handler=log_handler_a) as client_a,
            Client(stateful_proxy_server, log_handler=log_handler_b) as client_b,
        ):
            async with create_task_group() as tg:
                tg.start_soon(
                    client_a.call_tool,
                    "log",
                    {"message": "Hello, world!", "level": "info", "logger": "a"},
                )
                tg.start_soon(
                    client_b.call_tool,
                    "log",
                    {"message": "Hello, world!", "level": "info", "logger": "b"},
                )

        assert results["logger_a"].logger == "a"
        assert results["logger_b"].logger == "b"

    async def test_stateful_proxy(self, stateful_proxy_server: FastMCP):
        """Test that the state shared across multiple calls for the same client (fixes #959)."""
        async with Client(stateful_proxy_server) as client:
            with pytest.raises(ToolError, match="Value not found"):
                await client.call_tool("stateful_get", {})

            await client.call_tool("stateful_put", {"value": 1})
            result = await client.call_tool("stateful_get", {})
            assert result.data == 1

    async def test_stateless_proxy(self, stateless_server: str):
        """Test that the state will not be shared across different calls,
        even if they are from the same client."""
        async with Client(stateless_server) as client:
            await client.call_tool("stateful_put", {"value": 1})

            with pytest.raises(ToolError, match="Value not found"):
                await client.call_tool("stateful_get", {})

    async def test_multi_proxies_no_mixing(self):
        """Test that the stateful proxy client won't be mixed in multi-proxies sessions."""
        mcp_a, mcp_b = FastMCP(), FastMCP()

        @mcp_a.tool
        def tool_a() -> str:
            return "a"

        @mcp_b.tool
        def tool_b() -> str:
            return "b"

        proxy_mcp_a = FastMCPProxy(
            client_factory=StatefulProxyClient(mcp_a).new_stateful
        )
        proxy_mcp_b = FastMCPProxy(
            client_factory=StatefulProxyClient(mcp_b).new_stateful
        )
        multi_proxy_mcp = FastMCP()
        multi_proxy_mcp.mount(proxy_mcp_a, namespace="a")
        multi_proxy_mcp.mount(proxy_mcp_b, namespace="b")

        async with Client(multi_proxy_mcp) as client:
            result_a = await client.call_tool("a_tool_a", {})
            result_b = await client.call_tool("b_tool_b", {})
            assert result_a.data == "a"
            assert result_b.data == "b"
