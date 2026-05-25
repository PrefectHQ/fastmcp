"""Tests for on_connect and on_disconnect middleware hooks."""

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.middleware import Middleware


class SessionTrackingMiddleware(Middleware):
    """Records session connect/disconnect events."""

    def __init__(self):
        self.connected_sessions: list[str] = []
        self.disconnected_sessions: list[str] = []

    async def on_connect(self, context: Context) -> None:
        self.connected_sessions.append(context.session_id)

    async def on_disconnect(self, context: Context) -> None:
        self.disconnected_sessions.append(context.session_id)


@pytest.fixture
def mcp_with_tracker():
    mcp = FastMCP("test")
    tracker = SessionTrackingMiddleware()
    mcp.add_middleware(tracker)

    @mcp.tool()
    def ping() -> str:
        return "pong"

    return mcp, tracker


@pytest.mark.anyio
async def test_on_connect_fires_on_session_start(mcp_with_tracker):
    mcp, tracker = mcp_with_tracker

    async with Client(mcp) as client:
        assert len(tracker.connected_sessions) == 1

    assert len(tracker.connected_sessions) == 1


@pytest.mark.anyio
async def test_on_disconnect_fires_on_session_end(mcp_with_tracker):
    mcp, tracker = mcp_with_tracker

    async with Client(mcp) as client:
        pass  # connect then disconnect

    assert len(tracker.disconnected_sessions) == 1


@pytest.mark.anyio
async def test_session_id_is_consistent_across_connect_and_tool_call(mcp_with_tracker):
    """session_id in on_connect should match session_id available in tools."""
    mcp, tracker = mcp_with_tracker
    tool_session_ids: list[str] = []

    @mcp.tool()
    def get_session_id(ctx: Context) -> str:
        tool_session_ids.append(ctx.session_id)
        return ctx.session_id

    async with Client(mcp) as client:
        await client.call_tool("get_session_id", {})

    assert len(tracker.connected_sessions) == 1
    assert len(tool_session_ids) == 1
    assert tracker.connected_sessions[0] == tool_session_ids[0]


@pytest.mark.anyio
async def test_on_connect_fires_per_session(mcp_with_tracker):
    """Each session should trigger its own on_connect."""
    mcp, tracker = mcp_with_tracker

    async with Client(mcp):
        pass

    async with Client(mcp):
        pass

    assert len(tracker.connected_sessions) == 2
    assert len(tracker.disconnected_sessions) == 2
    # Each session gets a unique ID
    assert tracker.connected_sessions[0] != tracker.connected_sessions[1]


@pytest.mark.anyio
async def test_on_disconnect_fires_even_on_error(mcp_with_tracker):
    """on_disconnect should fire even if the session ends unexpectedly."""
    mcp, tracker = mcp_with_tracker

    try:
        async with Client(mcp) as client:
            raise RuntimeError("simulate crash")
    except RuntimeError:
        pass

    assert len(tracker.disconnected_sessions) == 1


@pytest.mark.anyio
async def test_multiple_middleware_all_notified(mcp_with_tracker):
    """All registered middleware should have on_connect/on_disconnect called."""
    mcp, tracker1 = mcp_with_tracker
    tracker2 = SessionTrackingMiddleware()
    mcp.add_middleware(tracker2)

    async with Client(mcp):
        pass

    assert len(tracker1.connected_sessions) == 1
    assert len(tracker2.connected_sessions) == 1
    assert tracker1.connected_sessions[0] == tracker2.connected_sessions[0]
