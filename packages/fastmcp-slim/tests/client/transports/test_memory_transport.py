"""Tests for the in-memory FastMCPTransport.

These tests verify transport-level behavior that affects all tests using
Client(server) with an in-process FastMCP server.
"""

import time

import pytest

from fastmcp import Client, FastMCP


@pytest.mark.timeout(10)
async def test_task_teardown_does_not_hang():
    """In-memory transport must tear down in under 2 seconds after a task call.

    This is a regression test for a teardown ordering bug where the Docket
    Worker shutdown would hang for 5 seconds on every test that used
    task=True. The root cause was the server lifespan (which owns the Docket
    Worker) being torn down BEFORE the task group (which owns the server's
    run() and all its pub/sub subscriptions). Fakeredis blocking operations
    held by those subscriptions prevented the Worker's internal TaskGroup
    from cancelling its children, causing a 5-second stall until the
    Client's move_on_after(5) timeout fired.

    The fix is to nest the task group INSIDE the lifespan context so that
    all server tasks (and their fakeredis resources) are cancelled and
    drained before Docket teardown begins.

    If this test takes ~5 seconds, the context manager nesting in
    FastMCPTransport.connect_session() has been reversed — the lifespan
    must be the OUTER context and the task group must be the INNER context.
    """
    mcp = FastMCP("teardown-test")

    @mcp.tool(task=True)
    async def fast_tool(x: int) -> int:
        return x * 2

    t0 = time.monotonic()

    async with Client(mcp) as client:
        task = await client.call_tool("fast_tool", {"x": 21}, task=True)
        result = await task.result()
        assert result.data == 42

    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, (
        f"Client teardown took {elapsed:.1f}s — expected <2s. "
        f"This usually means the context manager nesting in "
        f"FastMCPTransport.connect_session() is wrong: the lifespan "
        f"must be the OUTER context and the task group the INNER context. "
        f"See the comment in memory.py for details."
    )
