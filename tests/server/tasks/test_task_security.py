"""
Tests for session-based task ID isolation (CRITICAL SECURITY).

Ensures that tasks are properly scoped to sessions and clients cannot
access each other's tasks.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client


@pytest.fixture
async def task_server():
    """Create a server with background tasks enabled."""
    mcp = FastMCP("security-test-server")

    @mcp.tool(task=True)  # Enable background execution
    async def secret_tool(data: str) -> str:
        """A tool that processes sensitive data."""
        return f"Secret result: {data}"

    return mcp


async def test_same_session_can_access_all_its_tasks(task_server):
    """A single session can access all tasks it created."""
    async with Client(task_server) as client:
        # Submit multiple tasks
        task1 = await client.call_tool(
            "secret_tool", {"data": "first"}, task=True, task_id="task-1"
        )
        task2 = await client.call_tool(
            "secret_tool", {"data": "second"}, task=True, task_id="task-2"
        )

        # Wait for both to complete
        await task1.wait(timeout=2.0)
        await task2.wait(timeout=2.0)

        # Should be able to access both
        result1 = await task1.result()
        result2 = await task2.result()

        assert "first" in str(result1.data)
        assert "second" in str(result2.data)
