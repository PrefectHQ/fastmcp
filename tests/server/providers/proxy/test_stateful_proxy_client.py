import asyncio
from dataclasses import dataclass

import pytest
from anyio import create_task_group
from mcp.types import LoggingLevel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.server.providers.proxy import FastMCPProxy, StatefulProxyClient
from fastmcp.utilities.tests import find_available_port, run_server_async


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
                proxy_url, elicitation_handler=elicitation_handler
            ) as client:
                result1 = await client.call_tool("ask_name", {})
                assert result1.data == "Hello, Alice!"
                # Second call reuses the stateful session — this is the
                # one that would hang without the fix.
                result2 = await client.call_tool("ask_name", {})
                assert result2.data == "Hello, Alice!"


class TestRestoreRequestContext:
    """Regression tests for the Context lifecycle in `_restore_request_context`.

    Previously `_restore_request_context` called `_current_context.set(Context(fastmcp))`
    directly, bypassing `Context.__aenter__`.  That left `Context._tokens` empty,
    `_current_server` unset, and the matching `__aexit__` a no-op (refs #4054).
    """

    async def test_restore_enters_context_and_sets_current_server(self):
        """The fresh Context must be entered so `_current_context` and
        `_current_server` are both populated, and `_tokens` is non-empty so
        the eventual `__aexit__` actually releases the ContextVar."""
        import weakref
        from unittest.mock import MagicMock

        from mcp.server.lowlevel.server import request_ctx

        from fastmcp import FastMCP
        from fastmcp.server.context import _current_context
        from fastmcp.server.dependencies import _current_server
        from fastmcp.server.providers.proxy import _restore_request_context

        fastmcp = FastMCP("test")

        # Stand-in RequestContext — only identity / attribute access is exercised.
        rc = MagicMock()
        rc.session = MagicMock()
        rc.request_id = "req-1"

        rc_ref: list = [(rc, weakref.ref(fastmcp))]

        # No prior request_ctx in this task; _restore_request_context should
        # take the LookupError branch, set request_ctx and enter the Context.
        ctx = await _restore_request_context(rc_ref)
        try:
            assert ctx is not None, (
                "Context should be entered when fastmcp weakref is alive"
            )
            # __aenter__ must have populated _tokens (so __aexit__ does work)
            assert ctx._tokens, (
                "Context.__aenter__ was not called: _tokens is empty"
            )
            # _current_context must point at the freshly entered Context
            assert _current_context.get() is ctx
            # _current_server must be set (only happens via __aenter__)
            srv_ref = _current_server.get()
            assert srv_ref is not None and srv_ref() is fastmcp
            # And request_ctx must have been restored from the stash.
            assert request_ctx.get() is rc
        finally:
            await ctx.__aexit__(None, None, None) if ctx else None

        # __aexit__ must release the ContextVars cleanly.
        assert _current_context.get(None) is not ctx

    async def test_make_restoring_handler_awaits_aenter_and_aexit(self):
        """The wrapper produced by `_make_restoring_handler` must await
        `Context.__aenter__` before the handler runs and `__aexit__` after,
        so handlers see a fully-initialised Context and tokens don't leak.
        """
        import weakref
        from unittest.mock import MagicMock

        from mcp.server.lowlevel.server import request_ctx

        from fastmcp import FastMCP
        from fastmcp.server.context import Context, _current_context
        from fastmcp.server.providers.proxy import _make_restoring_handler

        fastmcp = FastMCP("test")

        rc = MagicMock()
        rc.session = MagicMock()
        rc.request_id = "req-2"

        rc_ref: list = [(rc, weakref.ref(fastmcp))]

        seen: dict = {}

        async def handler(*args, **kwargs):
            # Inside the handler the fresh Context must already be active.
            cur = _current_context.get(None)
            seen["context_active"] = isinstance(cur, Context)
            seen["tokens_populated"] = bool(cur and cur._tokens)
            return "ok"

        wrapped = _make_restoring_handler(handler, rc_ref)

        result = await wrapped()

        assert result == "ok"
        assert seen.get("context_active"), (
            "handler did not see an active Context (aenter was not awaited)"
        )
        assert seen.get("tokens_populated"), (
            "Context._tokens was empty inside handler (aenter bypassed)"
        )
        # After the wrapper returns, __aexit__ should have released the token,
        # so _current_context no longer points at our restored Context.
        assert _current_context.get(None) is None

    async def test_restore_returns_none_when_rc_ref_empty(self):
        """If nothing is stashed in `rc_ref`, no Context should be entered
        (and therefore nothing for the caller to clean up)."""
        from fastmcp.server.providers.proxy import _restore_request_context

        rc_ref: list = [None]
        ctx = await _restore_request_context(rc_ref)
        assert ctx is None
