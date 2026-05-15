"""
Tests for client-side tool task methods.

Tests the client's tool-specific task functionality, parallel to
test_client_prompt_tasks.py and test_client_resource_tasks.py.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.tasks import ToolTask
from fastmcp.exceptions import ToolError


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


async def test_immediate_tool_task_respects_raise_on_error_true():
    """Immediate task fallback should still raise ToolError when requested."""
    mcp = FastMCP("immediate-tool-task-error")

    @mcp.tool
    def failing_tool() -> str:
        raise ValueError("immediate task failure")

    async with Client(mcp) as client:
        task = await client.call_tool("failing_tool", task=True, raise_on_error=True)

        assert task.returned_immediately
        with pytest.raises(
            ToolError, match="does not support task-augmented execution"
        ):
            await task.result()


async def test_immediate_tool_task_respects_raise_on_error_false():
    """Immediate task fallback should return error results when requested."""
    mcp = FastMCP("immediate-tool-task-no-raise")

    @mcp.tool
    def failing_tool() -> str:
        raise ValueError("immediate task failure")

    async with Client(mcp) as client:
        task = await client.call_tool("failing_tool", task=True, raise_on_error=False)

        assert task.returned_immediately
        result = await task.result()
        assert result.is_error is True
        assert "does not support task-augmented execution" in str(result)


async def test_background_tool_task_respects_raise_on_error_true():
    """Background tasks should still raise ToolError by default on errors."""
    mcp = FastMCP("background-tool-task-error")

    @mcp.tool(task=True)
    async def failing_tool() -> str:
        raise ValueError("background task failure")

    async with Client(mcp) as client:
        task = await client.call_tool("failing_tool", task=True, raise_on_error=True)

        assert not task.returned_immediately
        with pytest.raises(ToolError, match="background task failure"):
            await task.result()


async def test_background_tool_task_respects_raise_on_error_false():
    """Background tasks should return error results when raise_on_error is disabled."""
    mcp = FastMCP("background-tool-task-no-raise")

    @mcp.tool(task=True)
    async def failing_tool() -> str:
        raise ValueError("background task failure")

    async with Client(mcp) as client:
        task = await client.call_tool("failing_tool", task=True, raise_on_error=False)

        assert not task.returned_immediately
        result = await task.result()
        assert result.is_error is True
        assert "background task failure" in str(result)
