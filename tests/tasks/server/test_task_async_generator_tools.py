"""Async-generator tools run as background tasks and match their sync result.

`TaskConfig.validate_function` decided with `is_coroutine_function`, which is
False for an `async def` containing `yield`, so an async-generator tool was
rejected as a sync function. Accepting it is only half the fix: the task worker
awaits the callable's result, and calling an async-generator function returns
the generator rather than an awaitable, so every task completed with
"object async_generator can't be used in 'await' expression". The worker now
materializes the generator the same way the synchronous path does.
"""

from __future__ import annotations

from typing import Any

import pytest

from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    call_tool_without_optin,
    run_task,
    running_task_server,
)


def _text(content: list[Any] | None) -> str | None:
    """Content is objects on the sync path and plain dicts on the task path."""
    if not content:
        return None
    first = content[0]
    return first["text"] if isinstance(first, dict) else first.text


async def _sync_outcome(
    server: FastMCP, name: str, arguments: dict[str, Any]
) -> tuple[str | None, bool]:
    try:
        result = await call_tool_without_optin(server, name, arguments)
    except Exception as exc:  # noqa: BLE001 - a tool error is an outcome here
        return str(exc), True
    return _text(result.content), False


async def _task_outcome(
    server: FastMCP, name: str, arguments: dict[str, Any]
) -> tuple[str | None, bool]:
    task = await run_task(server, name, arguments)
    assert task.status == "completed"
    return _text(task.result.get("content")), bool(task.result.get("isError"))


# --- registration ---------------------------------------------------------


async def test_async_generator_tool_with_task_true_is_registered() -> None:
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def stream():
        yield "a"

    tool = await mcp.get_tool("stream")
    assert isinstance(tool, FunctionTool)
    assert tool.task_config.mode == "optional"


async def test_async_generator_tool_with_inherited_task_true_is_registered() -> None:
    mcp = FastMCP("test", tasks=True)

    @mcp.tool
    async def stream():
        yield "a"

    tool = await mcp.get_tool("stream")
    assert isinstance(tool, FunctionTool)
    assert tool.task_config.mode == "optional"


async def test_async_coroutine_tool_with_task_true_is_registered() -> None:
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def async_tool(x: int) -> int:
        return x * 2

    tool = await mcp.get_tool("async_tool")
    assert isinstance(tool, FunctionTool)
    assert tool.task_config.mode == "optional"


async def test_sync_generator_tool_with_task_true_still_raises() -> None:
    mcp = FastMCP("test")

    with pytest.raises(ValueError, match="uses a sync function"):

        @mcp.tool(task=True)
        def stream():
            yield "a"


async def test_sync_function_with_task_true_still_raises() -> None:
    mcp = FastMCP("test")

    with pytest.raises(ValueError, match="uses a sync function"):

        @mcp.tool(task=True)
        def sync_tool(x: int) -> int:
            return x * 2


# --- end to end -----------------------------------------------------------


def _task_server() -> FastMCP:
    mcp = FastMCP("generator-tasks")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def agen(n: int):
        for i in range(n):
            yield f"item{i}"

    @mcp.tool(task=True)
    async def agen_typed(n: int) -> list[int]:
        for i in range(n):
            yield i

    @mcp.tool(task=True)
    async def agen_raises(n: int):
        yield "first"
        raise ValueError("boom")

    @mcp.tool(task=True)
    async def coroutine(n: int) -> int:
        return n * 2

    return mcp


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("agen", {"n": 3}),
        ("agen", {"n": 0}),
        ("agen_typed", {"n": 3}),
        ("agen_raises", {"n": 1}),
        ("coroutine", {"n": 3}),
    ],
)
async def test_task_result_matches_the_sync_result(
    name: str, arguments: dict[str, Any]
) -> None:
    """A task runs the generator body once and reports what the sync path does."""
    server = _task_server()

    async with running_task_server(server):
        sync = await _sync_outcome(server, name, arguments)
        task = await _task_outcome(server, name, arguments)

    assert task == sync
