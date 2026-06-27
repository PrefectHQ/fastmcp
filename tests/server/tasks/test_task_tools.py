"""
Tests for server-side tool task behavior.

Tests tool-specific task handling, parallel to test_task_prompts.py
and test_task_resources.py.
"""

import asyncio
import functools

import mcp.types
import pytest
from pydantic import BaseModel

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.messages import MessageHandler
from fastmcp.client.tasks import ToolTask
from fastmcp.exceptions import ToolError
from fastmcp.tools.function_tool import _resolve_param_hints


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


class _Item(BaseModel):
    value: str


async def test_task_tool_validates_model_arguments():
    """Model-typed args are coerced to model instances for task calls (#4349).

    The synchronous path validates arguments through the function's
    TypeAdapter, so a parameter typed as a Pydantic model arrives as a model
    instance. The task path must coerce the same way rather than passing the
    raw dict through to the function.
    """
    mcp = FastMCP("tool-task-validation-server")

    @mcp.tool(task=True)
    async def inspect_items(item: _Item, items: list[_Item]) -> dict[str, str]:
        return {"item": type(item).__name__, "element": type(items[0]).__name__}

    arguments = {"item": {"value": "a"}, "items": [{"value": "b"}]}
    expected = {"item": "_Item", "element": "_Item"}

    async with Client(mcp) as client:
        sync_result = await client.call_tool("inspect_items", arguments)
        task = await client.call_tool("inspect_items", arguments, task=True)
        task_result = await task.result()

    assert sync_result.data == expected
    assert task_result.data == expected


async def test_task_tool_invalid_arguments_fail_before_task_state():
    """Invalid task arguments are rejected before any task state is created.

    Coercion runs up front in submit_to_docket, so a validation failure surfaces
    before the task's Redis metadata and initial "working" status notification
    are written. Otherwise an invalid input would orphan a task the client had
    already observed via that notification.
    """

    class _Recorder(MessageHandler):
        def __init__(self):
            super().__init__()
            self.methods: list[str] = []

        async def on_notification(self, message: mcp.types.ServerNotification) -> None:
            self.methods.append(message.root.method)

    server = FastMCP("tool-task-invalid-args-server")

    @server.tool(task=True)
    async def needs_item(item: _Item) -> str:
        return item.value

    recorder = _Recorder()
    async with Client(server, message_handler=recorder) as client:
        # `item` is missing its required `value` field.
        task = await client.call_tool("needs_item", {"item": {}}, task=True)
        assert task.returned_immediately
        with pytest.raises(ToolError):
            await task.result()

    assert "notifications/tasks/status" not in recorder.methods


def test_resolve_param_hints_handles_partials():
    """Partials aren't introspectable by get_type_hints; resolve via the func.

    Argument coercion must not raise for partial-wrapped callables — it should
    resolve hints for the still-unbound parameters.
    """

    async def base(prefix: str, items: list[_Item]) -> str:
        return prefix

    partial_fn = functools.partial(base, "bound")
    hints = _resolve_param_hints(partial_fn)

    assert hints["items"] == list[_Item]


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
