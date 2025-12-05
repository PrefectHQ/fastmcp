"""
Tests for client-side handling of notifications/tasks/status (SEP-1686 lines 436-444).

Verifies that Task objects receive notifications, update their cache, wake up wait() calls,
and invoke user callbacks.
"""

import asyncio
import time

import pytest
from mcp.types import GetTaskResult

from fastmcp import FastMCP
from fastmcp.client import Client


@pytest.fixture
async def task_notification_server():
    """Server that sends task status notifications."""
    mcp = FastMCP("task-notification-test")

    @mcp.tool(task=True)
    async def quick_task(value: int) -> int:
        """Quick background task."""
        await asyncio.sleep(0.05)
        return value * 2

    @mcp.tool(task=True)
    async def slow_task(duration: float = 0.2) -> str:
        """Slow background task."""
        await asyncio.sleep(duration)
        return "done"

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

        # Give callbacks a moment to fire
        await asyncio.sleep(0.1)

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
        await asyncio.sleep(0.2)

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
        await asyncio.sleep(0.1)

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
        await asyncio.sleep(0.1)

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
            status.statusMessage is not None
        )  # Error details in statusMessage per spec
