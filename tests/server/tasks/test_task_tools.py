"""
Tests for server-side tool task behavior.

Tests tool-specific task handling, parallel to test_task_prompts.py
and test_task_resources.py.
"""

import asyncio

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.tasks import ToolTask


@pytest.fixture
async def tool_server():
    """Create a FastMCP server with task-enabled tools."""
    mcp = FastMCP("tool-task-server")

    @mcp.tool(task=True)
    async def simple_tool(message: str) -> str:
        """A simple tool for testing."""
        return f"Processed: {message}"

    @mcp.tool(task=False)
    async def sync_only_tool(message: str) -> str:
        """Tool with task=False."""
        return f"Sync: {message}"

    return mcp


async def test_synchronous_tool_call_unchanged(tool_server):
    """Tools without task metadata execute synchronously as before."""
    async with Client(tool_server) as client:
        # Regular call without task metadata
        result = await client.call_tool("simple_tool", {"message": "hello"})

        # Should execute immediately and return result
        assert "Processed: hello" in str(result)


async def test_tool_with_task_metadata_returns_immediately(tool_server):
    """Tools with task metadata return immediately with ToolTask object."""
    async with Client(tool_server) as client:
        # Call with task metadata
        task = await client.call_tool("simple_tool", {"message": "test"}, task=True)
        assert task
        assert not task.returned_immediately

        assert isinstance(task, ToolTask)
        assert isinstance(task.task_id, str)
        assert len(task.task_id) > 0


async def test_tool_task_executes_in_background(tool_server):
    """Tool task is submitted to Docket and executes in background."""
    execution_started = asyncio.Event()
    execution_completed = asyncio.Event()

    @tool_server.tool(task=True)
    async def coordinated_tool() -> str:
        """Tool with coordination points."""
        execution_started.set()
        await execution_completed.wait()
        return "completed"

    async with Client(tool_server) as client:
        task = await client.call_tool("coordinated_tool", task=True)
        assert task
        assert not task.returned_immediately

        # Wait for execution to start
        await asyncio.wait_for(execution_started.wait(), timeout=2.0)

        # Task should still be working
        status = await task.status()
        assert status.status in ["working"]

        # Signal completion
        execution_completed.set()
        await task.wait(timeout=2.0)

        result = await task.result()
        assert result.data == "completed"


async def test_forbidden_mode_tool_rejects_task_calls(tool_server):
    """Tools with task=False (mode=forbidden) reject task-augmented calls."""
    async with Client(tool_server) as client:
        # Calling with task=True when task=False should return error
        task = await client.call_tool("sync_only_tool", {"message": "test"}, task=True)
        assert task
        assert task.returned_immediately

        result = await task.result()
        # New behavior: mode="forbidden" returns an error
        assert result.is_error
        assert "does not support task-augmented execution" in str(result)
