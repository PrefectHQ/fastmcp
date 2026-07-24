"""Server-side tool task behavior for SEP-2663 tasks.

Covers task=True/False decoration, argument coercion parity between the
synchronous and task-submission paths (including the strict-validation flag),
immediate task metadata on submission, background execution with status polling,
and the rule that a forbidden (task=False) tool runs synchronously even when the
caller opts into tasks. Driven in-process via the task helpers because there is
no client task-submission API until Phase 4.
"""

from __future__ import annotations

import asyncio
import functools

import pytest
from fastmcp_tasks.models import CreateTaskResult
from pydantic import BaseModel

from fastmcp import FastMCP
from fastmcp.exceptions import ValidationError
from fastmcp.tools.function_tool import _resolve_param_hints
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    _opted_in_request,
    auth_scope,
    call_tool_without_optin,
    get_task,
    run_task,
    running_task_server,
    submit_task,
    wait_for_task,
)


class _Item(BaseModel):
    value: str


async def _opted_in_call(server: FastMCP, name: str, arguments: dict | None = None):
    """Run a `tools/call` WITH the tasks opt-in bound (used to prove sync paths)."""
    with auth_scope(None), _opted_in_request(name, arguments or {}, None):
        return await server.call_tool(name, arguments or {})


def _tool_server() -> FastMCP:
    mcp = FastMCP("tool-task-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def simple_tool(message: str) -> str:
        return f"Processed: {message}"

    @mcp.tool(task=False)
    async def sync_only_tool(message: str) -> str:
        return f"Sync: {message}"

    return mcp


# ---------------------------------------------------------------------------
# Argument coercion parity
# ---------------------------------------------------------------------------


async def test_task_tool_coerces_model_arguments():
    """Model-typed args are coerced to model instances on the task path (#4349).

    The synchronous path validates arguments through the function's TypeAdapter,
    so a parameter typed as a Pydantic model arrives as a model instance. The
    task path must coerce identically rather than passing the raw dict through.
    """
    mcp = FastMCP("tool-task-validation-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def inspect_items(item: _Item, items: list[_Item]) -> dict[str, str]:
        return {"item": type(item).__name__, "element": type(items[0]).__name__}

    arguments = {"item": {"value": "a"}, "items": [{"value": "b"}]}
    expected = {"item": "_Item", "element": "_Item"}
    async with running_task_server(mcp):
        sync_result = await call_tool_without_optin(mcp, "inspect_items", arguments)
        final = await run_task(mcp, "inspect_items", arguments)

    assert sync_result.structured_content == expected
    assert final.result is not None
    assert final.result["structuredContent"] == expected


async def test_task_arguments_are_coerced_like_sync_path():
    """A string-for-int arg coerces on the task path exactly as on the sync path."""
    mcp = FastMCP("coerce-task-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def square(n: int) -> int:
        return n * n

    async with running_task_server(mcp):
        final = await run_task(mcp, "square", {"n": "1"})
    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 1}


async def test_task_submission_honors_strict_input_validation():
    """Strict input validation rejects lax coercion on the task path too.

    With ``strict_input_validation=True`` a lax coercion like ``{"n": "1"}`` for
    an ``int`` parameter is rejected on the synchronous path. Task submission must
    reject it identically rather than silently coercing and queueing it.
    """
    mcp = FastMCP("strict-task-server", strict_input_validation=True)
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def square(n: int) -> int:
        return n * n

    async with running_task_server(mcp):
        # Sync path rejects the string-for-int coercion under strict validation.
        with pytest.raises(ValidationError):
            await call_tool_without_optin(mcp, "square", {"n": "1"})
        # Task submission must reject it too, before any task state is created.
        with pytest.raises(ValidationError):
            await submit_task(mcp, "square", {"n": "1"})


async def test_valid_argument_submits_under_strict_validation():
    """A well-typed argument still submits fine when strict validation is on."""
    mcp = FastMCP("strict-task-valid-server", strict_input_validation=True)
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def square(n: int) -> int:
        return n * n

    async with running_task_server(mcp):
        final = await run_task(mcp, "square", {"n": 4})
    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 16}


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


# ---------------------------------------------------------------------------
# Decoration and execution
# ---------------------------------------------------------------------------


async def test_synchronous_tool_call_without_opt_in():
    """A tool called without a tasks opt-in executes synchronously as before."""
    mcp = _tool_server()
    async with running_task_server(mcp):
        result = await call_tool_without_optin(mcp, "simple_tool", {"message": "hello"})
        assert not isinstance(result, CreateTaskResult)
        assert result.structured_content == {"result": "Processed: hello"}


async def test_tool_task_returns_metadata_immediately():
    """Submitting a task returns task metadata with a server-generated id."""
    mcp = _tool_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "simple_tool", {"message": "test"})
        assert isinstance(created, CreateTaskResult)
        assert isinstance(created.task_id, str)
        assert created.task_id
        assert created.status == "working"


async def test_tool_task_executes_in_background():
    """A submitted task runs in the background and can be polled to completion."""
    mcp = FastMCP("bg-server")
    mcp.add_extension(TasksExtension())
    started = asyncio.Event()
    finish = asyncio.Event()

    @mcp.tool(task=True)
    async def coordinated() -> str:
        started.set()
        await finish.wait()
        return "completed"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "coordinated", {})
        await asyncio.wait_for(started.wait(), timeout=2.0)
        working = await get_task(mcp, created.task_id)
        assert working.status == "working"
        finish.set()
        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {"result": "completed"}


async def test_forbidden_tool_runs_sync_even_with_opt_in():
    """A task=False tool runs synchronously even when the caller opts into tasks."""
    mcp = _tool_server()
    async with running_task_server(mcp):
        result = await _opted_in_call(mcp, "sync_only_tool", {"message": "test"})
        assert not isinstance(result, CreateTaskResult)
        assert result.structured_content == {"result": "Sync: test"}
