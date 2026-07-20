"""Client session and task error propagation tests."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession
from mcp_types import TextContent

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import PythonStdioTransport
from fastmcp.client.transports.base import TransportOptions


class TestSessionTaskErrorPropagation:
    """Tests for ensuring session task errors propagate to client calls.

    Regression tests for https://github.com/PrefectHQ/fastmcp/issues/2595
    where the client would hang indefinitely when the session task failed
    (e.g., due to HTTP 4xx/5xx errors) instead of raising an exception.
    """

    async def test_session_task_error_propagates_to_call(self, fastmcp_server):
        """Test that errors in session task propagate to pending client calls.

        When the session task fails (e.g., due to HTTP errors), pending
        client operations should immediately receive the exception rather
        than hanging indefinitely.
        """
        client = Client(fastmcp_server)

        async with client:
            original_task = client._session_state.session_task
            assert original_task is not None

            async def never_complete():
                """A coroutine that will never complete normally."""
                await asyncio.Event().wait()

            async def failing_session():
                """Simulates a session task that raises an error."""
                raise ValueError("Simulated HTTP error")

            # Replace session_task with one that will fail
            client._session_state.session_task = asyncio.create_task(failing_session())

            # The monitoring should detect the session task failure
            with pytest.raises(ValueError, match="Simulated HTTP error"):
                await client._await_with_session_monitoring(never_complete())

            # Restore original task for cleanup
            client._session_state.session_task = original_task

    async def test_session_task_already_done_with_error(self, fastmcp_server):
        """Test that if session task is already done with error, calls fail immediately."""
        client = Client(fastmcp_server)

        async with client:
            original_task = client._session_state.session_task

            async def raise_error():
                raise ValueError("Session failed")

            # Replace session_task with one that has already failed
            failed_task = asyncio.create_task(raise_error())
            try:
                await failed_task
            except ValueError:
                pass  # Expected
            client._session_state.session_task = failed_task

            # New calls should fail immediately with the original error
            async def simple_coro():
                return "should not reach"

            with pytest.raises(ValueError, match="Session failed"):
                await client._await_with_session_monitoring(simple_coro())

            # Restore original task for cleanup
            client._session_state.session_task = original_task

    async def test_session_task_already_done_no_error_raises_runtime_error(
        self, fastmcp_server
    ):
        """Test that if session task completes without error, raises RuntimeError."""
        client = Client(fastmcp_server)

        async with client:
            original_task = client._session_state.session_task

            # Create a task that completes normally (unexpected for session task)
            completed_task = asyncio.create_task(asyncio.sleep(0))
            await completed_task
            client._session_state.session_task = completed_task

            async def simple_coro():
                return "should not reach"

            with pytest.raises(
                RuntimeError, match="Session task completed unexpectedly"
            ):
                await client._await_with_session_monitoring(simple_coro())

            # Restore original task for cleanup
            client._session_state.session_task = original_task

    async def test_normal_operation_unaffected(self, fastmcp_server):
        """Test that normal operation is unaffected by the monitoring."""
        client = Client(fastmcp_server)

        async with client:
            # These should all work normally
            tools = await client.list_tools()
            assert len(tools) > 0

            result = await client.call_tool("greet", {"name": "Test"})
            assert "Hello, Test!" in str(result.content)

            resources = await client.list_resources()
            assert len(resources) > 0

            prompts = await client.list_prompts()
            assert len(prompts) > 0

    async def test_no_session_task_falls_back_to_direct_await(self, fastmcp_server):
        """Test that when no session task exists, it falls back to direct await."""
        client = Client(fastmcp_server)

        async with client:
            # Temporarily remove session_task to test fallback
            original_task = client._session_state.session_task
            client._session_state.session_task = None

            # Should work via direct await
            async def simple_coro():
                return "success"

            result = await client._await_with_session_monitoring(simple_coro())
            assert result == "success"

            # Restore for cleanup
            client._session_state.session_task = original_task


class TestCustomSessionClass:
    """Transports build the session class the client asks for."""

    async def test_session_class_is_used_when_provided(self):
        built: list[str] = []

        class RecordingClientSession(ClientSession):
            def __init__(self, *args, **kwargs):
                built.append("yes")
                super().__init__(*args, **kwargs)

        server = FastMCP("Server")

        @server.tool
        def ping() -> str:
            return "pong"

        client = Client(server)
        client._transport_options = TransportOptions(
            session_class=RecordingClientSession
        )
        async with client:
            await client.call_tool("ping")

        assert built == ["yes"]

    async def test_default_session_class_is_client_session(self):
        server = FastMCP("Server")

        @server.tool
        def ping() -> str:
            return "pong"

        client = Client(server)
        assert TransportOptions().session_class is ClientSession
        async with client:
            result = await client.call_tool("ping")

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "pong"


class TestKeepAliveSessionsRespectClientOptions:
    """A cached stdio session must not be handed to a client wanting different options.

    `StdioTransport` keeps its subprocess and session alive between connections
    by default. Serving that cached session to a second client would give it the
    first client's behavior — e.g. an ordinary client silently inheriting a
    proxy's non-validating session. Rebuilding it is only safe while nobody else
    is using it.
    """

    class Reconnected(Exception):
        """Raised in place of a real teardown so the test stops at the guard."""

    @asynccontextmanager
    async def cached_connection(self, monkeypatch, options: TransportOptions):
        """A transport that believes it already holds a session built for `options`."""
        transport = PythonStdioTransport(script_path=__file__, keep_alive=True)

        async def never_finishes():
            await asyncio.sleep(60)

        task = asyncio.create_task(never_finishes())
        transport._connect_task = task
        transport._session_options = options

        async def fake_disconnect():
            raise TestKeepAliveSessionsRespectClientOptions.Reconnected

        monkeypatch.setattr(transport, "disconnect", fake_disconnect)
        try:
            yield transport
        finally:
            task.cancel()
            transport._connect_task = None

    async def test_matching_options_reuse_the_cached_session(self, monkeypatch):
        options = TransportOptions()
        async with self.cached_connection(monkeypatch, options) as transport:
            assert await transport.connect(transport_options=options) is None

    async def test_differing_options_rebuild_an_idle_session(self, monkeypatch):
        class OtherSession(ClientSession):
            pass

        async with self.cached_connection(monkeypatch, TransportOptions()) as transport:
            with pytest.raises(self.Reconnected):
                await transport.connect(
                    transport_options=TransportOptions(session_class=OtherSession)
                )

    async def test_differing_options_do_not_disturb_a_session_in_use(self, monkeypatch):
        """Tearing down a live session would break the client already on it."""

        class OtherSession(ClientSession):
            pass

        async with self.cached_connection(monkeypatch, TransportOptions()) as transport:
            transport._active_sessions = 1

            with pytest.raises(RuntimeError, match="still using it"):
                await transport.connect(
                    transport_options=TransportOptions(session_class=OtherSession)
                )

            assert transport._connect_task is not None
