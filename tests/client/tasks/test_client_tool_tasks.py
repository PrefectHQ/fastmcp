"""
Tests for client-side tool task methods.

Tests the client's tool-specific task functionality, parallel to
test_client_prompt_tasks.py and test_client_resource_tasks.py.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.tasks import ToolTask


@pytest.fixture
async def tool_task_server():
    """Create a test server with task-enabled tools."""
    mcp = FastMCP("tool-task-test")

    @mcp.tool(task=True)
    async def echo(message: str) -> str:
        """Echo back the message."""
        return f"Echo: {message}"

    @mcp.tool(task=True)
    async def multiply(a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b

    return mcp


async def test_call_tool_as_task_returns_tool_task(tool_task_server):
    """call_tool with task=True returns a ToolTask object."""
    async with Client(tool_task_server) as client:
        task = await client.call_tool("echo", {"message": "hello"}, task=True)

        assert isinstance(task, ToolTask)
        assert isinstance(task.task_id, str)
        assert len(task.task_id) > 0


async def test_tool_task_server_generated_id(tool_task_server):
    """call_tool with task=True gets server-generated task ID."""
    async with Client(tool_task_server) as client:
        task = await client.call_tool("echo", {"message": "test"}, task=True)

        # Server should generate a UUID task ID
        assert task.task_id is not None
        assert isinstance(task.task_id, str)
        # UUIDs have hyphens
        assert "-" in task.task_id


async def test_tool_task_result_returns_call_tool_result(tool_task_server):
    """ToolTask.result() returns CallToolResult with tool data."""
    async with Client(tool_task_server) as client:
        task = await client.call_tool("multiply", {"a": 6, "b": 7}, task=True)
        assert not task.returned_immediately

        result = await task.result()
        assert result.data == 42


async def test_tool_task_await_syntax(tool_task_server):
    """Tool tasks can be awaited directly to get result."""
    async with Client(tool_task_server) as client:
        task = await client.call_tool("multiply", {"a": 7, "b": 6}, task=True)

        # Can await task directly (syntactic sugar for task.result())
        result = await task
        assert result.data == 42


async def test_tool_task_status_and_wait(tool_task_server):
    """ToolTask.status() returns GetTaskResult."""
    async with Client(tool_task_server) as client:
        task = await client.call_tool("echo", {"message": "test"}, task=True)

        status = await task.status()
        assert status.taskId == task.task_id
        assert status.status in ["working", "completed"]

        # Wait for completion
        await task.wait(timeout=2.0)
        final_status = await task.status()
        assert final_status.status == "completed"
