"""Tests for FastMCP Progress dependency (SEP-2663 tasks)."""

import asyncio
import json

from mcp_types import TextContent

from fastmcp import FastMCP
from fastmcp.server.dependencies import Progress
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    call_tool_without_optin,
    running_task_server,
    submit_task,
    wait_for_task,
)


async def test_progress_in_immediate_execution():
    """Progress dependency works when a tool runs synchronously."""
    mcp = FastMCP("test")

    @mcp.tool
    async def test_tool(progress: Progress = Progress()) -> str:
        await progress.set_total(10)
        await progress.increment()
        await progress.set_message("Testing")
        return "done"

    result = await call_tool_without_optin(mcp, "test_tool", {})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "done"


async def test_progress_in_background_task():
    """Progress dependency works inside a background task."""
    mcp = FastMCP("test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def test_task(progress: Progress = Progress()) -> str:
        await progress.set_total(5)
        await progress.increment()
        await progress.set_message("Step 1")
        return "done"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "test_task", {})
        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {"result": "done"}


async def test_progress_tracks_multiple_increments():
    """Progress correctly tracks multiple increment calls."""
    mcp = FastMCP("test")

    @mcp.tool
    async def count_to_ten(progress: Progress = Progress()) -> str:
        await progress.set_total(10)
        for _ in range(10):
            await progress.increment()
        return "counted"

    result = await call_tool_without_optin(mcp, "count_to_ten", {})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "counted"


async def test_progress_status_message_in_background_task():
    """A working task surfaces the current progress message as statusMessage."""
    mcp = FastMCP("test")
    mcp.add_extension(TasksExtension())
    release = asyncio.Event()

    @mcp.tool(task=True)
    async def task_with_progress(progress: Progress = Progress()) -> str:
        await progress.set_total(3)
        await progress.set_message("Step 1 of 3")
        await progress.increment()
        await release.wait()
        await progress.set_message("Step 2 of 3")
        await progress.increment()
        return "done"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "task_with_progress", {})

        # The task parks on `release` while working; its statusMessage should
        # reflect the progress message (or be None, depending on the poll race).
        working = await wait_for_task(
            mcp, created.task_id, target_states=frozenset({"working"})
        )
        msg = working.status_message
        assert msg is None or msg.startswith("Step")

        release.set()
        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {"result": "done"}


async def test_inmemory_progress_state():
    """In-memory progress stores and returns state correctly."""
    mcp = FastMCP("test")

    @mcp.tool
    async def test_tool(progress: Progress = Progress()) -> dict:
        assert progress.current is None
        assert progress.total == 1
        assert progress.message is None

        await progress.set_total(10)
        assert progress.total == 10

        await progress.increment()
        assert progress.current == 1

        await progress.increment(2)
        assert progress.current == 3

        await progress.set_message("Testing")
        assert progress.message == "Testing"

        return {
            "current": progress.current,
            "total": progress.total,
            "message": progress.message,
        }

    result = await call_tool_without_optin(mcp, "test_tool", {})
    assert isinstance(result.content[0], TextContent)
    state = json.loads(result.content[0].text)
    assert state["current"] == 3
    assert state["total"] == 10
    assert state["message"] == "Testing"
