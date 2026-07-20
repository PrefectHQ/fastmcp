"""
Tests for client-side handling of notifications/tasks/status (SEP-1686 lines 436-444).

Verifies that Task objects receive notifications, update their cache, wake up wait() calls,
and invoke user callbacks.
"""

import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timezone

import pytest
from mcp_types import GetTaskResult

from fastmcp import FastMCP
from fastmcp.client import Client


async def _wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll until condition() is true or timeout elapses.

    Used in place of a fixed sleep when waiting for an async callback or
    notification to be delivered/dispatched after the awaited call returns.
    """
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        await asyncio.sleep(0.005)


@pytest.fixture
async def task_notification_server():
    """Server that sends task status notifications."""
    mcp = FastMCP("task-notification-test")

    @mcp.tool(task=True)
    async def quick_task(value: int) -> int:
        """Quick background task with a brief, measurable delay (contrast with instant_task)."""
        await asyncio.sleep(0.01)
        return value * 2

    @mcp.tool(task=True)
    async def instant_task(value: int) -> int:
        """Background task that completes with no delay."""
        return value * 2

    @mcp.tool(task=True)
    async def failing_task() -> str:
        """Task that fails."""
        raise ValueError("Intentional failure")

    return mcp


async def test_task_receives_status_notification(task_notification_server):
    """Task object receives and processes status notifications."""
    async with Client(task_notification_server) as client:
        task = await client.call_tool("quick_task", {"value": 5}, task=True)

        # Wait for task to complete (notification should arrive)
        status = await task.wait(timeout=2.0)

        # Verify task completed
        assert status.status == "completed"


async def test_status_cache_updated_by_notification(task_notification_server):
    """Cached status is updated when notification arrives."""
    async with Client(task_notification_server) as client:
        task = await client.call_tool("quick_task", {"value": 10}, task=True)

        # Wait for completion (notification should update cache)
        await task.wait(timeout=2.0)

        # Status should be cached (no server call needed)
        # Call status() twice - should return same cached object
        status1 = await task.status()
        status2 = await task.status()

        # Should be the exact same object (from cache)
        assert status1 is status2
        assert status1.status == "completed"


async def test_callback_invoked_on_notification(task_notification_server):
    """User callback is invoked when notification arrives."""
    callback_invocations = []

    def status_callback(status: GetTaskResult):
        """Sync callback."""
        callback_invocations.append(status)

    async with Client(task_notification_server) as client:
        task = await client.call_tool("quick_task", {"value": 7}, task=True)

        # Register callback
        task.on_status_change(status_callback)

        # Wait for completion
        await task.wait(timeout=2.0)

        # Wait for the status this test actually asserts on. Waiting merely for
        # "some callback fired" would be satisfied by the earlier `working`
        # notification and race the `completed` one.
        await _wait_until(
            lambda: any(s.status == "completed" for s in callback_invocations)
        )

    # Callback should have been invoked at least once
    assert len(callback_invocations) > 0

    # Should have received completed status
    completed_statuses = [s for s in callback_invocations if s.status == "completed"]
    assert len(completed_statuses) > 0


async def test_async_callback_invoked(task_notification_server):
    """Async callback is invoked when notification arrives."""
    callback_invocations = []

    async def async_status_callback(status: GetTaskResult):
        """Async callback."""
        await asyncio.sleep(0.01)  # Simulate async work
        callback_invocations.append(status)

    async with Client(task_notification_server) as client:
        task = await client.call_tool("quick_task", {"value": 3}, task=True)

        # Register async callback
        task.on_status_change(async_status_callback)

        # Wait for completion
        await task.wait(timeout=2.0)

        # Give async callbacks time to complete
        await _wait_until(lambda: len(callback_invocations) > 0)

    # Async callback should have been invoked
    assert len(callback_invocations) > 0


async def test_multiple_callbacks_all_invoked(task_notification_server):
    """Multiple callbacks are all invoked."""
    callback1_calls = []
    callback2_calls = []

    def callback1(status: GetTaskResult):
        callback1_calls.append(status.status)

    def callback2(status: GetTaskResult):
        callback2_calls.append(status.status)

    async with Client(task_notification_server) as client:
        task = await client.call_tool("quick_task", {"value": 8}, task=True)

        task.on_status_change(callback1)
        task.on_status_change(callback2)

        await task.wait(timeout=2.0)
        await _wait_until(lambda: bool(callback1_calls) and bool(callback2_calls))

    # Both callbacks should have been invoked
    assert len(callback1_calls) > 0
    assert len(callback2_calls) > 0


async def test_callback_error_doesnt_break_notification(task_notification_server):
    """Callback errors don't prevent other callbacks from running."""
    callback1_calls = []
    callback2_calls = []

    def failing_callback(status: GetTaskResult):
        callback1_calls.append("called")
        raise ValueError("Callback intentionally fails")

    def working_callback(status: GetTaskResult):
        callback2_calls.append(status.status)

    async with Client(task_notification_server) as client:
        task = await client.call_tool("quick_task", {"value": 12}, task=True)

        task.on_status_change(failing_callback)
        task.on_status_change(working_callback)

        await task.wait(timeout=2.0)
        await _wait_until(lambda: bool(callback1_calls) and bool(callback2_calls))

    # Failing callback was called (and errored)
    assert len(callback1_calls) > 0

    # Working callback should still have been invoked
    assert len(callback2_calls) > 0


async def test_wait_wakes_early_on_notification(task_notification_server):
    """wait() wakes up immediately when notification arrives, not after poll interval."""
    async with Client(task_notification_server) as client:
        task = await client.call_tool("quick_task", {"value": 15}, task=True)

        # Record timing
        start = time.time()
        status = await task.wait(timeout=5.0)
        elapsed = time.time() - start

        # Should complete much faster than the fallback poll interval (500ms)
        # With notifications, should be < 200ms for quick task
        # Without notifications, would take 500ms+ due to polling
        assert elapsed < 1.0  # Very generous bound
        assert status.status == "completed"


async def test_notification_with_failed_task(task_notification_server):
    """Notifications work for failed tasks too."""
    async with Client(task_notification_server) as client:
        task = await client.call_tool("failing_task", {}, task=True)

        with pytest.raises(Exception):
            await task

        # Should have cached the failed status from notification
        status = await task.status()
        assert status.status == "failed"
        assert (
            status.status_message is not None
        )  # Error details in statusMessage per spec


async def test_fast_task_completion_delivered_via_notification(
    task_notification_server,
):
    """A near-instant task still delivers its completion via a status notification.

    Regression test for the Docket subscribe() setup-window race: a task that
    finishes before the pub/sub subscription goes live had its terminal state
    publish lost, so no completion notification ever reached the client and
    wait() fell back to a full poll interval. The server now reconciles the
    execution against Redis to close that gap.

    Callbacks fire only for received notifications — client-side polling updates
    the status cache directly without invoking them — so a "completed" callback
    proves the notification path (not the poll fallback) was exercised.
    """
    received: list[str] = []

    async with Client(task_notification_server) as client:
        task = await client.call_tool("instant_task", {"value": 21}, task=True)
        task.on_status_change(lambda status: received.append(status.status))

        result = await task
        assert result.data == 42

        # Allow the completion notification to arrive and dispatch.
        await _wait_until(lambda: "completed" in received)

    assert "completed" in received


async def test_wait_returns_on_input_required(task_notification_server):
    """wait() should return immediately when task enters input_required, not hang."""
    async with Client(task_notification_server) as client:
        task = await client.call_tool("quick_task", {"value": 1}, task=True)

        # Directly inject an input_required status into the cache and signal the event.
        # SDK v2 types the Task timestamps as ISO 8601 strings.
        now = datetime.now(timezone.utc).isoformat()
        input_required_status = GetTaskResult(
            task_id=task._task_id,
            status="input_required",
            status_message="Waiting for user input",
            created_at=now,
            last_updated_at=now,
            ttl=None,
        )
        task._status_cache = input_required_status
        if task._status_event is None:
            task._status_event = asyncio.Event()
        task._status_event.set()

        # Should return immediately with input_required, not hang for 300s
        status = await task.wait(timeout=2.0)
        assert status.status == "input_required"
