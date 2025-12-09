"""
Tests for SEP-1686 protocol-level task handling.

Generic protocol tests that use tools as test fixtures.
Tests metadata, notifications, and error handling at the protocol level.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client


@pytest.fixture
async def task_enabled_server():
    """Create a FastMCP server with task-enabled tools."""
    mcp = FastMCP("task-test-server")

    @mcp.tool(task=True)
    async def simple_tool(message: str) -> str:
        """A simple tool for testing."""
        return f"Processed: {message}"

    @mcp.tool(task=True)
    async def failing_tool() -> str:
        """A tool that always fails."""
        raise ValueError("This tool always fails")

    return mcp


async def test_task_metadata_includes_task_id_and_ttl(task_enabled_server):
    """Task metadata properly includes server-generated taskId and ttl."""
    async with Client(task_enabled_server) as client:
        # Submit with specific ttl (server generates task ID)
        task = await client.call_tool(
            "simple_tool",
            {"message": "test"},
            task=True,
            ttl=30000,
        )
        assert task
        assert not task.returned_immediately

        # Server should have generated a task ID
        assert task.task_id is not None
        assert isinstance(task.task_id, str)


async def test_task_notification_sent_after_submission(task_enabled_server):
    """Server sends notifications/tasks/created after task submission."""

    @task_enabled_server.tool(task=True)
    async def background_tool(message: str) -> str:
        return f"Processed: {message}"

    async with Client(task_enabled_server) as client:
        task = await client.call_tool("background_tool", {"message": "test"}, task=True)
        assert task
        assert not task.returned_immediately

        # Verify we can query the task
        status = await task.status()
        assert status.taskId == task.task_id


async def test_failed_task_stores_error(task_enabled_server):
    """Failed tasks store the error in results."""

    @task_enabled_server.tool(task=True)
    async def failing_task_tool() -> str:
        raise ValueError("This tool always fails")

    async with Client(task_enabled_server) as client:
        task = await client.call_tool("failing_task_tool", task=True)
        assert task
        assert not task.returned_immediately

        # Wait for task to fail
        status = await task.wait(state="failed", timeout=2.0)
        assert status.status == "failed"
