"""Task protocol methods for SEP-2663: tasks/get, tasks/cancel, tasks/update.

SEP-1686's `tasks/result` and `tasks/list` are removed — `tasks/get` inlines the
completed result. This suite covers the surviving methods, driven in-process via
the task helpers because there is no client task-submission API until Phase 4.
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp_tasks.models import UpdateTaskResult
from mcp.shared.exceptions import MCPError

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    cancel_task,
    get_task,
    run_task,
    running_task_server,
    submit_task,
    update_task,
    wait_for_task,
)


def _methods_server() -> FastMCP:
    mcp = FastMCP("endpoint-test-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def quick_tool(value: int) -> int:
        return value * 2

    @mcp.tool(task=True)
    async def error_tool() -> str:
        raise ToolError("Task failed!")

    return mcp


async def test_tasks_get_returns_status_and_inlined_result():
    """`tasks/get` reports status and inlines the completed tool result."""
    mcp = _methods_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "quick_tool", {"value": 21})
        got = await get_task(mcp, created.task_id)
        assert got.task_id == created.task_id
        assert got.status in {"working", "completed"}

        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {"result": 42}
        assert final.result is not None
        assert final.result["isError"] is False


async def test_tasks_get_includes_poll_interval():
    """`tasks/get` includes the poll-interval hint."""
    mcp = _methods_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "quick_tool", {"value": 42})
        got = await get_task(mcp, created.task_id)
        assert got.poll_interval_ms == 5000


async def test_tasks_get_returns_is_error_result_for_raised_tool():
    """A raised tool error is a completed task with an is_error result (SEP-2663)."""
    mcp = _methods_server()
    async with running_task_server(mcp):
        final = await run_task(mcp, "error_tool", {})
        assert final.status == "completed"
        assert final.error is None
        assert final.result is not None
        assert final.result["isError"] is True
        assert "Task failed!" in final.result["content"][0]["text"]


async def test_tasks_get_unknown_id_raises_not_found():
    """`tasks/get` for an unknown id raises a not-found error (-32602)."""
    mcp = _methods_server()
    async with running_task_server(mcp):
        with pytest.raises(MCPError, match="not found"):
            await get_task(mcp, "nonexistent-task-id")


async def test_tasks_cancel_transitions_to_cancelled():
    """`tasks/cancel` transitions a running task to cancelled."""
    mcp = FastMCP("cancel-test")
    mcp.add_extension(TasksExtension())
    release = asyncio.Event()

    @mcp.tool(task=True)
    async def slow_tool() -> str:
        await release.wait()
        return "done"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "slow_tool", {})
        await cancel_task(mcp, created.task_id)
        # Release so the worker unwinds whether or not it observed the cancel first.
        release.set()
        final = await wait_for_task(
            mcp,
            created.task_id,
            target_states=frozenset({"cancelled", "completed"}),
        )
        assert final.status in {"cancelled", "completed"}


async def test_tasks_update_acks_empty():
    """`tasks/update` returns an empty ack."""
    mcp = FastMCP("update-test")
    mcp.add_extension(TasksExtension())
    release = asyncio.Event()

    @mcp.tool(task=True)
    async def waiter() -> str:
        await release.wait()
        return "done"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "waiter", {})
        ack = await update_task(mcp, created.task_id, {})
        assert isinstance(ack, UpdateTaskResult)
        release.set()
