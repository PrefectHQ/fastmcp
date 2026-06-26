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
        task = await client.call_tool(
            "sync_only_tool", {"message": "test"}, task=True, raise_on_error=False
        )
        assert task
        assert task.returned_immediately

        result = await task.result()
        # New behavior: mode="forbidden" returns an error
        assert result.is_error
        assert "does not support task-augmented execution" in str(result)


async def test_tool_task_runs_setup_and_teardown(tool_server):
    """Task-enabled tools run setup before execution and teardown after."""
    events: list[str] = []

    def setup_hook() -> None:
        events.append("setup")

    def teardown_hook() -> None:
        events.append("teardown")

    @tool_server.tool(task=True, setup=setup_hook, teardown=teardown_hook)
    async def hooked_task_tool() -> str:
        events.append("tool")
        return "ok"

    async with Client(tool_server) as client:
        task = await client.call_tool("hooked_task_tool", task=True)
        await task.wait(timeout=2.0)

        result = await task.result()

    assert result.data == "ok"
    assert events == ["setup", "tool", "teardown"]


async def test_tool_task_teardown_receives_raw_result(tool_server):
    """Task-enabled tool teardown receives the raw tool result."""
    received_result = None

    def teardown_hook(result: str) -> None:
        nonlocal received_result
        received_result = result

    @tool_server.tool(task=True, teardown=teardown_hook)
    async def hooked_task_tool() -> str:
        return "raw-ok"

    async with Client(tool_server) as client:
        task = await client.call_tool("hooked_task_tool", task=True)
        await task.wait(timeout=2.0)

        result = await task.result()

    assert result.data == "raw-ok"
    assert received_result == "raw-ok"


async def test_tool_task_uses_pydantic_argument_coercion(tool_server):
    """Task-enabled tools validate and coerce arguments like foreground calls."""

    @tool_server.tool(task=True)
    async def increment(count: int) -> int:
        return count + 1

    async with Client(tool_server) as client:
        task = await client.call_tool("increment", {"count": "41"}, task=True)
        await task.wait(timeout=2.0)

        result = await task.result()

    assert result.data == 42


async def test_tool_task_argument_model_is_cached(tool_server, monkeypatch):
    """Task argument coercion reuses its generated Pydantic model."""
    import fastmcp.tools.function_tool as function_tool

    create_model_calls = 0
    original_create_model = function_tool.create_model

    def tracking_create_model(*args, **kwargs):
        nonlocal create_model_calls
        create_model_calls += 1
        return original_create_model(*args, **kwargs)

    monkeypatch.setattr(function_tool, "create_model", tracking_create_model)

    @tool_server.tool(task=True)
    async def increment(count: int) -> int:
        return count + 1

    async with Client(tool_server) as client:
        first_task = await client.call_tool("increment", {"count": "1"}, task=True)
        await first_task.wait(timeout=2.0)
        first_result = await first_task.result()

        second_task = await client.call_tool("increment", {"count": "2"}, task=True)
        await second_task.wait(timeout=2.0)
        second_result = await second_task.result()

    assert first_result.data == 2
    assert second_result.data == 3
    assert create_model_calls == 1


async def test_tool_task_hooks_preserve_current_docket_dependency(tool_server):
    """Task hooks do not break CurrentDocket dependency injection."""
    from fastmcp.server.dependencies import CurrentDocket

    events: list[str] = []

    def setup_hook() -> None:
        events.append("setup")

    def teardown_hook() -> None:
        events.append("teardown")

    @tool_server.tool(task=True, setup=setup_hook, teardown=teardown_hook)
    async def docket_tool(docket=CurrentDocket()) -> str:
        events.append("tool")
        assert docket is not None
        return "ok"

    async with Client(tool_server) as client:
        task = await client.call_tool("docket_tool", task=True)
        await task.wait(timeout=2.0)

        result = await task.result()

    assert result.data == "ok"
    assert events == ["setup", "tool", "teardown"]
