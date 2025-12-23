"""
Tests for task protocol methods.

Tests the tasks/get, tasks/result, and tasks/list JSON-RPC protocol methods.
"""

import asyncio

import pytest
from mcp.shared.exceptions import McpError

from fastmcp import FastMCP
from fastmcp.client import Client


@pytest.fixture
async def endpoint_server():
    """Create a server with background tasks and HTTP transport."""
    mcp = FastMCP("endpoint-test-server")

    @mcp.tool(task=True)  # Enable background execution
    async def quick_tool(value: int) -> int:
        """Returns the value immediately."""
        return value * 2

    @mcp.tool(task=True)  # Enable background execution
    async def error_tool() -> str:
        """Always raises an error."""
        raise RuntimeError("Task failed!")

    @mcp.tool(task=True)  # Enable background execution
    async def slow_tool() -> str:
        """A slow tool for testing cancellation."""
        await asyncio.sleep(10)
        return "done"

    return mcp


async def test_tasks_get_endpoint_returns_status(endpoint_server):
    """POST /tasks/get returns task status."""
    async with Client(endpoint_server) as client:
        # Submit a task
        task = await client.call_tool("quick_tool", {"value": 21}, task=True)

        # Check status immediately - should be submitted or working
        status = await task.status()
        assert status.taskId == task.task_id
        assert status.status in ["working", "completed"]

        # Wait for completion
        await task.wait(timeout=2.0)

        # Check again - should be completed
        status = await task.status()
        assert status.status == "completed"


async def test_tasks_get_endpoint_includes_poll_interval(endpoint_server):
    """Task status includes pollFrequency hint."""
    async with Client(endpoint_server) as client:
        task = await client.call_tool("quick_tool", {"value": 42}, task=True)

        status = await task.status()
        assert status.pollInterval is not None
        assert isinstance(status.pollInterval, int)


async def test_tasks_result_endpoint_returns_result_when_completed(endpoint_server):
    """POST /tasks/result returns the tool result when completed."""
    async with Client(endpoint_server) as client:
        task = await client.call_tool("quick_tool", {"value": 21}, task=True)

        # Wait for completion and get result
        result = await task.result()
        assert result.data == 42  # 21 * 2


async def test_tasks_result_endpoint_errors_if_not_completed(endpoint_server):
    """POST /tasks/result returns error if task not completed yet."""
    # Create a task that won't complete until signaled
    completion_signal = asyncio.Event()

    @endpoint_server.tool(task=True)  # Enable background execution
    async def blocked_tool() -> str:
        await completion_signal.wait()
        return "done"

    async with Client(endpoint_server) as client:
        task = await client.call_tool("blocked_tool", task=True)

        # Try to get result immediately (task still running)
        with pytest.raises(Exception):  # Should raise or return error
            await client.get_task_result(task.task_id)

        # Cleanup - signal completion
        completion_signal.set()


async def test_tasks_result_endpoint_errors_if_task_not_found(endpoint_server):
    """POST /tasks/result returns error for non-existent task."""
    async with Client(endpoint_server) as client:
        # Try to get result for non-existent task
        with pytest.raises(Exception):
            await client.get_task_result("non-existent-task-id")


async def test_tasks_result_endpoint_returns_error_for_failed_task(endpoint_server):
    """POST /tasks/result returns error information for failed tasks."""
    async with Client(endpoint_server) as client:
        task = await client.call_tool("error_tool", task=True)

        # Wait for task to fail
        await task.wait(state="failed", timeout=2.0)

        # Getting result should raise or return error info
        with pytest.raises(Exception) as exc_info:
            await task.result()

        assert (
            "failed" in str(exc_info.value).lower()
            or "error" in str(exc_info.value).lower()
        )


async def test_tasks_list_endpoint_session_isolation(endpoint_server):
    """list_tasks returns only tasks submitted by this client."""
    # Since client tracks tasks locally, this tests client-side tracking
    async with Client(endpoint_server) as client:
        # Submit multiple tasks (server generates IDs)
        tasks = []
        for i in range(3):
            task = await client.call_tool("quick_tool", {"value": i}, task=True)
            tasks.append(task)

        # Wait for all to complete
        for task in tasks:
            await task.wait(timeout=2.0)

        # List tasks - should see all 3
        response = await client.list_tasks()
        returned_ids = [t["taskId"] for t in response["tasks"]]
        task_ids = [t.task_id for t in tasks]
        assert len(returned_ids) == 3
        assert all(tid in task_ids for tid in returned_ids)


async def test_get_status_nonexistent_task_raises_error(endpoint_server):
    """Getting status for nonexistent task raises MCP error (per SEP-1686 SDK behavior)."""
    async with Client(endpoint_server) as client:
        # Try to get status for task that was never created
        # Per SDK implementation: raises ValueError which becomes JSON-RPC error
        with pytest.raises(McpError, match="Task nonexistent-task-id not found"):
            await client.get_task_status("nonexistent-task-id")


async def test_task_cancellation_workflow(endpoint_server):
    """Task can be cancelled, transitioning to cancelled state."""
    async with Client(endpoint_server) as client:
        # Submit slow task
        task = await client.call_tool("slow_tool", {}, task=True)

        # Give it a moment to start
        await asyncio.sleep(0.1)

        # Cancel the task
        await task.cancel()

        # Give cancellation a moment to process
        await asyncio.sleep(0.1)

        # Task should be in cancelled state
        status = await task.status()
        assert status.status == "cancelled"


async def test_task_cancellation_interrupts_running_coroutine(endpoint_server):
    """Task cancellation actually interrupts the running coroutine.

    This verifies that when a task is cancelled, the underlying asyncio
    coroutine receives CancelledError rather than continuing to completion.
    Requires pydocket >= 0.16.2.

    See: https://github.com/jlowin/fastmcp/issues/2679
    """
    started = asyncio.Event()
    was_interrupted = asyncio.Event()
    completed_normally = asyncio.Event()

    @endpoint_server.tool(task=True)
    async def interruptible_tool() -> str:
        started.set()
        try:
            await asyncio.sleep(60)
            completed_normally.set()
            return "completed"
        except asyncio.CancelledError:
            was_interrupted.set()
            raise

    async with Client(endpoint_server) as client:
        task = await client.call_tool("interruptible_tool", {}, task=True)

        # Wait for the tool to actually start executing
        await asyncio.wait_for(started.wait(), timeout=5.0)

        # Cancel the task
        await task.cancel()

        # Wait for cancellation to propagate
        await asyncio.wait_for(was_interrupted.wait(), timeout=5.0)

        # The coroutine should have been interrupted, not completed normally
        assert was_interrupted.is_set(), "Task was not interrupted by cancellation"
        assert not completed_normally.is_set(), (
            "Task completed instead of being cancelled"
        )
