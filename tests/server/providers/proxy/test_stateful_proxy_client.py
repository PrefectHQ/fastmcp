import asyncio
import weakref
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from anyio import create_task_group
from mcp_types import LoggingLevel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.context import _current_context
from fastmcp.server.dependencies import fastmcp_request_ctx, get_server
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.server.providers.proxy import (
    FastMCPProxy,
    StatefulProxyClient,
    _restore_request_context,
)
from fastmcp.utilities.tests import find_available_port, run_server_async


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP("TestServer")

    states: dict[str, int] = {}

    @mcp.tool
    async def log(
        message: str, level: LoggingLevel, logger: str, context: Context
    ) -> None:
        await context.log(message=message, level=level, logger_name=logger)

    @mcp.tool
    async def stateful_put(value: int, context: Context) -> None:
        """put a value associated with the server session"""
        # SDK v2 constructs a ServerSession per request, so `id(context.session)`
        # is not a stable per-connection key. Use the connection-scoped
        # `session_id` to share state across calls on the same client session.
        key = context.session_id
        states[key] = value

    @mcp.tool
    async def stateful_get(context: Context) -> int:
        """get the value associated with the server session"""
        key = context.session_id
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
            Client(
                stateful_proxy_server, mode="legacy", log_handler=log_handler_a
            ) as client_a,
            Client(
                stateful_proxy_server, mode="legacy", log_handler=log_handler_b
            ) as client_b,
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
        async with Client(stateful_proxy_server, mode="legacy") as client:
            with pytest.raises(ToolError, match="Value not found"):
                await client.call_tool("stateful_get", {})

            await client.call_tool("stateful_put", {"value": 1})
            result = await client.call_tool("stateful_get", {})
            assert result.data == 1

    async def test_stateless_proxy(self, stateless_server: str):
        """Test that the state will not be shared across different calls,
        even if they are from the same client."""
        async with Client(stateless_server, mode="legacy") as client:
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

        async with Client(multi_proxy_mcp, mode="legacy") as client:
            result_a = await client.call_tool("a_tool_a", {})
            result_b = await client.call_tool("b_tool_b", {})
            assert result_a.data == "a"
            assert result_b.data == "b"

    @pytest.mark.timeout(10)
    async def test_stateful_proxy_elicitation_over_http(self):
        """Elicitation through a stateful proxy over HTTP must not hang.

        When StatefulProxyClient reuses a session, the receive-loop task
        inherits a stale request_ctx ContextVar from the first request.
        The streamable-HTTP transport uses related_request_id to route
        server-initiated messages (like elicitation) back to the correct
        HTTP response stream.  A stale request_id routes to a closed
        stream, causing the elicitation to hang forever.

        This test runs the proxy over HTTP (not in-process) so the
        transport's related_request_id routing is exercised.
        """

        @dataclass
        class Person:
            name: str

        backend = FastMCP("backend")

        @backend.tool
        async def ask_name(ctx: Context) -> str:
            result = await ctx.elicit("What is your name?", response_type=Person)
            if isinstance(result, AcceptedElicitation):
                assert isinstance(result.data, Person)
                return f"Hello, {result.data.name}!"
            return "declined"

        stateful_client = StatefulProxyClient(backend)
        proxy = FastMCPProxy(
            client_factory=stateful_client.new_stateful,
            name="proxy",
        )

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content=response_type(name="Alice"))

        # Run the proxy over HTTP so the transport uses
        # related_request_id routing for server-initiated messages.
        async with run_server_async(proxy) as proxy_url:
            async with Client(
                proxy_url, mode="legacy", elicitation_handler=elicitation_handler
            ) as client:
                result1 = await client.call_tool("ask_name", {})
                assert result1.data == "Hello, Alice!"
                # Second call reuses the stateful session — this is the
                # one that would hang without the fix.
                result2 = await client.call_tool("ask_name", {})
                assert result2.data == "Hello, Alice!"


class TestRestoreRequestContextCurrentServer:
    """Regression tests for `_restore_request_context` (refs #4054, Bug 4).

    The receive-loop repair must also restore `_current_server`, so handlers
    that resolve the server via dependency injection (e.g. `get_server()`)
    work. It must do so set-only — without opening a `Context` context-manager
    scope — since this patches a long-lived task's ContextVars in place.
    """

    async def _run_in_child_context(self, fn):
        # Run in a child task so contextvar writes are isolated from the test
        # task and `fastmcp_request_ctx` is genuinely unset (defaults to None).
        return await asyncio.create_task(fn())

    async def test_lookup_error_branch_restores_current_server(self):
        fastmcp = FastMCP("restore-test")
        rc = MagicMock()
        rc.session = MagicMock()
        rc.request_id = "req-1"
        rc_ref: list = [(rc, weakref.ref(fastmcp))]

        async def body():
            assert fastmcp_request_ctx.get() is None
            _restore_request_context(rc_ref)

            # The actual Bug 4 fix: get_server() now resolves.
            assert get_server() is fastmcp
            assert fastmcp_request_ctx.get() is rc

            ctx = _current_context.get()
            assert ctx is not None
            assert ctx.fastmcp is fastmcp
            # Set-only: no context-manager scope was opened, so __aenter__'s
            # token bookkeeping never ran.
            assert ctx._tokens == []
            assert not hasattr(ctx, "_shared_context")

        await self._run_in_child_context(body)

    async def test_stale_override_branch_restores_current_server(self):
        fastmcp = FastMCP("restore-test")
        session = MagicMock()

        stale_rc = MagicMock()
        stale_rc.session = session
        stale_rc.request_id = "old"

        fresh_rc = MagicMock()
        fresh_rc.session = session
        fresh_rc.request_id = "new"

        rc_ref: list = [(fresh_rc, weakref.ref(fastmcp))]

        async def body():
            fastmcp_request_ctx.set(stale_rc)
            _restore_request_context(rc_ref)

            assert fastmcp_request_ctx.get() is fresh_rc
            assert get_server() is fastmcp

        await self._run_in_child_context(body)

    async def test_no_stash_is_noop(self):
        rc_ref: list = [None]

        async def body():
            # No stash: nothing restored, no error.
            _restore_request_context(rc_ref)
            assert fastmcp_request_ctx.get() is None

        await self._run_in_child_context(body)
