"""The explicit `ToolTask` handle (the return-quickly surface, SEP-2663).

`call_tool_task` returns a `ToolTask` as soon as the server accepts the task, so
the caller can do other work and drive it: `status`, `wait`, `result`, `cancel`,
or `await`. This contrasts with `client.call_tool`, which polls to completion
transparently. All tests use a real `Client(mode="auto")` over the in-memory
transport, since tasks are modern-only.
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp_tasks.models import MISSING_REQUIRED_CLIENT_CAPABILITY
from mcp.shared.exceptions import MCPError

from fastmcp import Context, FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension, ToolTask, call_tool_task


@pytest.fixture
def tool_task_server() -> FastMCP:
    mcp = FastMCP("tool-task-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def echo(message: str) -> str:
        return f"Echo: {message}"

    @mcp.tool(task=True)
    async def multiply(a: int, b: int) -> int:
        return a * b

    @mcp.tool(task=True)
    async def boom() -> str:
        raise ValueError("background task failure")

    return mcp


async def test_call_tool_task_returns_tool_task(tool_task_server: FastMCP):
    async with Client(tool_task_server, mode="auto") as client:
        task = await call_tool_task(client, "echo", {"message": "hello"})

        assert isinstance(task, ToolTask)
        assert isinstance(task.task_id, str)
        assert task.task_id


async def test_tool_task_result_returns_parsed_result(tool_task_server: FastMCP):
    async with Client(tool_task_server, mode="auto") as client:
        task = await call_tool_task(client, "multiply", {"a": 6, "b": 7})
        result = await task.result()
        assert result.data == 42


async def test_tool_task_await_syntax(tool_task_server: FastMCP):
    async with Client(tool_task_server, mode="auto") as client:
        task = await call_tool_task(client, "multiply", {"a": 7, "b": 6})
        result = await task
        assert result.data == 42


async def test_tool_task_status_and_wait(tool_task_server: FastMCP):
    async with Client(tool_task_server, mode="auto") as client:
        task = await call_tool_task(client, "echo", {"message": "test"})

        status = await task.status()
        assert status.task_id == task.task_id
        assert status.status in {"working", "completed"}

        final = await task.wait(timeout=2.0)
        assert final.status == "completed"


async def test_tool_task_result_is_cached(tool_task_server: FastMCP):
    """Repeated result() calls return the same cached object without re-polling."""
    async with Client(tool_task_server, mode="auto") as client:
        task = await call_tool_task(client, "multiply", {"a": 2, "b": 5})

        result1 = await task.result()
        result2 = await task.result()
        result3 = await task
        assert result1 is result2 is result3
        assert result1.data == 10


async def test_background_task_raises_on_error_by_default(tool_task_server: FastMCP):
    async with Client(tool_task_server, mode="auto") as client:
        task = await call_tool_task(client, "boom", {})
        with pytest.raises(ToolError, match="background task failure"):
            await task.result()


async def test_background_task_returns_error_when_not_raising(
    tool_task_server: FastMCP,
):
    async with Client(tool_task_server, mode="auto") as client:
        task = await call_tool_task(client, "boom", {}, raise_on_error=False)
        result = await task.result()
        assert result.is_error
        assert "background task failure" in str(result)


async def test_multiple_concurrent_tool_tasks(tool_task_server: FastMCP):
    async with Client(tool_task_server, mode="auto") as client:
        tasks = [
            (await call_tool_task(client, "multiply", {"a": i, "b": 2}), i * 2)
            for i in range(5)
        ]
        for task, expected in tasks:
            result = await task.result()
            assert result.data == expected


async def test_tool_task_cancel():
    """A long-running task can be cancelled through the handle."""
    mcp = FastMCP("cancel-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def forever(ctx: Context) -> str:
        await asyncio.Event().wait()
        return "never"

    async with Client(mcp, mode="auto") as client:
        task = await call_tool_task(client, "forever", {})
        await task.wait(state="working", timeout=2.0)
        await task.cancel()
        final = await task.wait(timeout=2.0)
        assert final.status == "cancelled"


async def test_required_mode_without_optin_raises_32003():
    """A legacy client never negotiates the tasks capability, so a required-mode
    tool call is rejected with the -32003 missing-capability error."""
    mcp = FastMCP("required-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=TaskConfig(mode="required"))
    async def must_task(x: int) -> int:
        return x

    async with Client(mcp, mode="legacy") as client:
        with pytest.raises(MCPError) as excinfo:
            await client.call_tool("must_task", {"x": 1})

    assert excinfo.value.error.code == MISSING_REQUIRED_CLIENT_CAPABILITY
