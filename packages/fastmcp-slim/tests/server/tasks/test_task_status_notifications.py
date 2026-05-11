"""
Tests for notifications/tasks/status subscription mechanism (SEP-1686 lines 436-444).

Per the spec, servers MAY send notifications/tasks/status when task state changes.
This is an optional optimization that reduces client polling frequency.

These tests verify that the subscription mechanism works correctly without breaking
existing functionality. Notification delivery is best-effort and clients MUST NOT
rely on receiving them.
"""

import asyncio

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client


@pytest.fixture
async def notification_server():
    """Create a server for testing task status notifications."""
    mcp = FastMCP("notification-test")

    @mcp.tool(task=True)
    async def quick_task(value: int) -> int:
        """Quick task that completes immediately."""
        return value * 2

    @mcp.tool(task=True)
    async def slow_task(duration: float = 0.1) -> str:
        """Slow task for testing working status."""
        await asyncio.sleep(duration)
        return "completed"

    @mcp.tool(task=True)
    async def failing_task() -> str:
        """Task that always fails."""
        raise ValueError("Task failed intentionally")

    @mcp.prompt(task=True)
    async def test_prompt(name: str) -> str:
        """Test prompt for background execution."""
        await asyncio.sleep(0.05)
        return f"Hello, {name}!"

    @mcp.resource("test://resource", task=True)
    async def test_resource() -> str:
        """Test resource for background execution."""
        await asyncio.sleep(0.05)
        return "resource content"

    return mcp


async def test_subscription_spawned_for_tool_task(notification_server: FastMCP):
    """Subscription task is spawned when tool task is created."""
    async with Client(notification_server) as client:
        # Create task - should spawn subscription
        task = await client.call_tool("quick_task", {"value": 5}, task=True)

        # Task should complete normally
        result = await task
        assert result.data == 10

        # Subscription should clean up automatically
        # (No way to directly test, but shouldn't cause issues)


async def test_subscription_handles_task_completion(notification_server: FastMCP):
    """Subscription properly handles task completion and cleanup."""
    async with Client(notification_server) as client:
        # Multiple tasks should each get their own subscription
        task1 = await client.call_tool("quick_task", {"value": 1}, task=True)
        task2 = await client.call_tool("quick_task", {"value": 2}, task=True)
        task3 = await client.call_tool("quick_task", {"value": 3}, task=True)

        # All should complete successfully
        result1 = await task1
        result2 = await task2
        result3 = await task3

        assert result1.data == 2
        assert result2.data == 4
        assert result3.data == 6

        # Subscriptions should all clean up
        # Give them a moment
        await asyncio.sleep(0.1)


async def test_subscription_handles_task_failure(notification_server: FastMCP):
    """Subscription properly handles task failure."""
    async with Client(notification_server) as client:
        task = await client.call_tool("failing_task", {}, task=True)

        # Task should fail
        with pytest.raises(Exception):
            await task

        # Subscription should handle failure and clean up
        await asyncio.sleep(0.1)


async def test_subscription_for_prompt_tasks(notification_server: FastMCP):
    """Subscriptions work for prompt tasks."""
    async with Client(notification_server) as client:
        task = await client.get_prompt("test_prompt", {"name": "World"}, task=True)

        result = await task
        # Prompt result has messages
        assert result

        # Subscription should clean up
        await asyncio.sleep(0.1)


async def test_subscription_for_resource_tasks(notification_server: FastMCP):
    """Subscriptions work for resource tasks."""
    async with Client(notification_server) as client:
        task = await client.read_resource("test://resource", task=True)

        result = await task
        assert result  # Resource contents

        # Subscription should clean up
        await asyncio.sleep(0.1)


async def test_subscriptions_cleanup_on_session_disconnect(
    notification_server: FastMCP,
):
    """Subscriptions are cleaned up when session disconnects."""
    # Start session and create task
    async with Client(notification_server) as client:
        task = await client.call_tool("slow_task", {"duration": 1.0}, task=True)
        task_id = task.task_id
        # Disconnect before task completes (session __aexit__ cancels subscriptions)

    # Session is now closed, subscription should be cancelled
    # Task continues in Docket but notification subscription is gone
    # This test passing means no crash occurred during cleanup
    assert task_id  # Task was created


async def test_multiple_concurrent_subscriptions(notification_server: FastMCP):
    """Multiple concurrent tasks each have their own subscription."""
    async with Client(notification_server) as client:
        # Start many tasks concurrently
        tasks = []
        for i in range(10):
            task = await client.call_tool("quick_task", {"value": i}, task=True)
            tasks.append(task)

        # All should complete
        results = await asyncio.gather(*tasks)
        assert len(results) == 10

        # All subscriptions should clean up
        await asyncio.sleep(0.1)
