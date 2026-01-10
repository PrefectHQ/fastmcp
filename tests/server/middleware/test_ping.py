"""Tests for ping middleware."""

from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.middleware.ping import PingMiddleware


class TestPingMiddlewareInit:
    """Test PingMiddleware initialization."""

    def test_init_default(self):
        """Test default initialization."""
        middleware = PingMiddleware()
        assert middleware.interval_ms == 30000
        assert middleware._active_sessions == set()

    def test_init_custom(self):
        """Test custom interval initialization."""
        middleware = PingMiddleware(interval_ms=5000)
        assert middleware.interval_ms == 5000

    def test_init_invalid_interval_zero(self):
        """Test that zero interval raises ValueError."""
        with pytest.raises(ValueError, match="interval_ms must be positive"):
            PingMiddleware(interval_ms=0)

    def test_init_invalid_interval_negative(self):
        """Test that negative interval raises ValueError."""
        with pytest.raises(ValueError, match="interval_ms must be positive"):
            PingMiddleware(interval_ms=-1000)


class TestPingMiddlewareOnMessage:
    """Test on_message hook behavior."""

    async def test_starts_ping_task_on_first_message(self):
        """Test that ping task is started on first message from a session."""
        middleware = PingMiddleware(interval_ms=1000)

        mock_session = MagicMock()
        mock_session._subscription_task_group = MagicMock()
        mock_session._subscription_task_group.start_soon = MagicMock()

        mock_context = MagicMock()
        mock_context.fastmcp_context.session = mock_session

        mock_call_next = AsyncMock(return_value="result")

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "result"
        assert id(mock_session) in middleware._active_sessions
        mock_session._subscription_task_group.start_soon.assert_called_once()

    async def test_does_not_start_duplicate_task(self):
        """Test that duplicate messages from same session don't spawn duplicate tasks."""
        middleware = PingMiddleware(interval_ms=1000)

        mock_session = MagicMock()
        mock_session._subscription_task_group = MagicMock()
        mock_session._subscription_task_group.start_soon = MagicMock()

        mock_context = MagicMock()
        mock_context.fastmcp_context.session = mock_session

        mock_call_next = AsyncMock(return_value="result")

        # First message
        await middleware.on_message(mock_context, mock_call_next)
        # Second message from same session
        await middleware.on_message(mock_context, mock_call_next)
        # Third message from same session
        await middleware.on_message(mock_context, mock_call_next)

        # Should only start task once
        assert mock_session._subscription_task_group.start_soon.call_count == 1

    async def test_starts_separate_task_per_session(self):
        """Test that different sessions get separate ping tasks."""
        middleware = PingMiddleware(interval_ms=1000)

        mock_session1 = MagicMock()
        mock_session1._subscription_task_group = MagicMock()
        mock_session1._subscription_task_group.start_soon = MagicMock()

        mock_session2 = MagicMock()
        mock_session2._subscription_task_group = MagicMock()
        mock_session2._subscription_task_group.start_soon = MagicMock()

        mock_context1 = MagicMock()
        mock_context1.fastmcp_context.session = mock_session1

        mock_context2 = MagicMock()
        mock_context2.fastmcp_context.session = mock_session2

        mock_call_next = AsyncMock(return_value="result")

        await middleware.on_message(mock_context1, mock_call_next)
        await middleware.on_message(mock_context2, mock_call_next)

        mock_session1._subscription_task_group.start_soon.assert_called_once()
        mock_session2._subscription_task_group.start_soon.assert_called_once()
        assert len(middleware._active_sessions) == 2

    async def test_skips_task_when_no_task_group(self):
        """Test graceful handling when session has no task group."""
        middleware = PingMiddleware(interval_ms=1000)

        mock_session = MagicMock()
        mock_session._subscription_task_group = None

        mock_context = MagicMock()
        mock_context.fastmcp_context.session = mock_session

        mock_call_next = AsyncMock(return_value="result")

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "result"
        # Session should NOT be added if task group is None
        assert id(mock_session) not in middleware._active_sessions

    async def test_skips_when_fastmcp_context_is_none(self):
        """Test that middleware passes through when fastmcp_context is None."""
        middleware = PingMiddleware(interval_ms=1000)

        mock_context = MagicMock()
        mock_context.fastmcp_context = None

        mock_call_next = AsyncMock(return_value="result")

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "result"
        assert len(middleware._active_sessions) == 0

    async def test_skips_when_request_context_is_none(self):
        """Test that middleware passes through when request_context is None."""
        middleware = PingMiddleware(interval_ms=1000)

        mock_context = MagicMock()
        mock_context.fastmcp_context = MagicMock()
        mock_context.fastmcp_context.request_context = None

        mock_call_next = AsyncMock(return_value="result")

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "result"
        assert len(middleware._active_sessions) == 0


class TestPingLoop:
    """Test the ping loop behavior."""

    async def test_ping_loop_sends_pings_at_interval(self):
        """Test that ping loop sends pings at configured interval."""
        middleware = PingMiddleware(interval_ms=50)

        mock_session = MagicMock()
        mock_session.send_ping = AsyncMock()

        session_id = id(mock_session)
        middleware._active_sessions.add(session_id)

        # Run ping loop for a short time then cancel
        with anyio.move_on_after(0.15):
            await middleware._ping_loop(mock_session, session_id)

        # Should have sent at least 2 pings in 150ms with 50ms interval
        assert mock_session.send_ping.call_count >= 2

    async def test_ping_loop_cleans_up_on_cancellation(self):
        """Test that session is removed from active sessions on cancellation."""
        middleware = PingMiddleware(interval_ms=50)

        mock_session = MagicMock()
        mock_session.send_ping = AsyncMock()

        session_id = 12345
        middleware._active_sessions.add(session_id)

        # Run and cancel the ping loop
        with anyio.move_on_after(0.1):
            await middleware._ping_loop(mock_session, session_id)

        # Session should be cleaned up after cancellation
        assert session_id not in middleware._active_sessions


class TestPingMiddlewareIntegration:
    """Integration tests for PingMiddleware with real FastMCP server."""

    async def test_ping_middleware_registers_session(self):
        """Test that PingMiddleware registers sessions on first request."""
        mcp = FastMCP("PingTestServer")
        middleware = PingMiddleware(interval_ms=50)
        mcp.add_middleware(middleware)

        @mcp.tool
        def hello() -> str:
            return "Hello!"

        assert len(middleware._active_sessions) == 0

        async with Client(mcp) as client:
            result = await client.call_tool("hello")
            assert result.content[0].text == "Hello!"

            # Should have registered the session
            assert len(middleware._active_sessions) == 1

            # Make another request - should not add duplicate
            await client.call_tool("hello")
            assert len(middleware._active_sessions) == 1

    async def test_ping_task_cancelled_on_disconnect(self):
        """Test that ping task is properly cancelled when client disconnects."""
        mcp = FastMCP("PingTestServer")
        middleware = PingMiddleware(interval_ms=50)
        mcp.add_middleware(middleware)

        @mcp.tool
        def hello() -> str:
            return "Hello!"

        async with Client(mcp) as client:
            await client.call_tool("hello")
            # Should have one active session
            assert len(middleware._active_sessions) == 1

        # After disconnect, give a moment for cleanup
        await anyio.sleep(0.01)

        # Session should be cleaned up
        assert len(middleware._active_sessions) == 0
