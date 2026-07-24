"""Tests for the in-memory FastMCPTransport.

These tests verify transport-level behavior that affects all tests using
Client(server) with an in-process FastMCP server.
"""

import time

import pytest
from docket import Docket

from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import submit_task, wait_for_task


def test_transport_repr_includes_server_name():
    transport = FastMCPTransport(FastMCP("repr-test"))

    assert repr(transport) == "<FastMCPTransport(server='repr-test')>"


@pytest.fixture
def reset_docket_memory_server():
    """Force a fresh memory:// Docket server bound to this test's loop."""
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")
    yield
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")


@pytest.mark.timeout(10)
async def test_task_teardown_does_not_hang(reset_docket_memory_server):
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

    There is no client task-submission API yet (Phase 4), so the task is
    driven server-side within the live in-memory session; the teardown path
    being exercised is the same either way.
    """
    mcp = FastMCP("teardown-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def fast_tool(x: int) -> int:
        return x * 2

    t0 = time.monotonic()

    async with Client(mcp):
        created = await submit_task(mcp, "fast_tool", {"x": 21})
        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {"result": 42}

    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, (
        f"Client teardown took {elapsed:.1f}s — expected <2s. "
        f"This usually means the context manager nesting in "
        f"FastMCPTransport.connect_session() is wrong: the lifespan "
        f"must be the OUTER context and the task group the INNER context. "
        f"See the comment in memory.py for details."
    )
