"""The guard-pattern reentrant loop driven inside a background task.

A `task=True` tool that *returns* an `InputRequiredResult` (rather than awaiting
`ctx.elicit()`) is the same guard authoring model FastMCP uses foreground. As a
task, the worker drives the multi-round-trip itself: it parks the request on the
poll surface, the client answers via `tasks/update`, and the tool is re-invoked
with the answer on `ctx.input_responses` — identical to the foreground contract,
only the transport differs. These tests exercise that loop end-to-end through
the real interceptor and handlers via `task_helpers`.
"""

from __future__ import annotations

from typing import Any

import mcp_types

from fastmcp import Context, FastMCP
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    cancel_task,
    get_task,
    running_task_server,
    submit_task,
    update_task,
    wait_for_task,
)


def _elicit_request(message: str) -> mcp_types.ElicitRequest:
    return mcp_types.ElicitRequest(
        params=mcp_types.ElicitRequestFormParams(
            message=message,
            requested_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        )
    )


def _answer(responses: mcp_types.InputResponses, key: str) -> str:
    """Read the string value a client accepted for `key` (test helper)."""
    result = responses[key]
    assert isinstance(result, mcp_types.ElicitResult)
    assert result.content is not None
    return str(result.content["value"])


def _input_required(
    requests: dict[str, mcp_types.ElicitRequest],
    request_state: str | None = None,
) -> mcp_types.InputRequiredResult:
    return mcp_types.InputRequiredResult(
        result_type="input_required",
        input_requests=requests,
        request_state=request_state,
    )


async def _park_key(mcp: FastMCP, task_id: str) -> str:
    parked = await wait_for_task(
        mcp, task_id, target_states=frozenset({"input_required"})
    )
    assert parked.status == "input_required"
    assert parked.input_requests is not None
    return next(iter(parked.input_requests))


async def test_cancel_parked_task_reports_cancelled_and_refuses_resume():
    """Cancelling an `input_required` task actually cancels it.

    A parked guard leg's Docket execution is already COMPLETED, so cancelling
    only that execution would leave `tasks/get` reporting `input_required`
    forever and let a later `tasks/update` resume the task. The logical
    cancellation marker must make `tasks/get` report `cancelled` and turn a
    subsequent answer into a no-op that never re-enters the tool.
    """
    mcp = FastMCP("guard-cancel")
    mcp.add_extension(TasksExtension())

    ran_after_cancel = False

    @mcp.tool(task=True)
    async def greet(ctx: Context) -> str | mcp_types.InputRequiredResult:
        nonlocal ran_after_cancel
        responses = ctx.input_responses
        if responses is None:
            return _input_required({"name": _elicit_request("Your name?")})
        ran_after_cancel = True
        return f"Hello, {_answer(responses, 'name')}!"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "greet", {})
        key = await _park_key(mcp, created.task_id)

        await cancel_task(mcp, created.task_id)
        cancelled = await get_task(mcp, created.task_id)
        assert cancelled.status == "cancelled"

        # Answering a cancelled task is an idempotent no-op: it must not resume.
        await update_task(
            mcp,
            created.task_id,
            {key: {"action": "accept", "content": {"value": "Ada"}}},
        )
        still_cancelled = await get_task(mcp, created.task_id)
        assert still_cancelled.status == "cancelled"

    assert ran_after_cancel is False


async def test_guard_return_single_round_completes():
    """A tool that returns InputRequiredResult once is driven to completion."""
    mcp = FastMCP("guard")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def greet(ctx: Context) -> str | mcp_types.InputRequiredResult:
        responses = ctx.input_responses
        if responses is None:
            return _input_required({"name": _elicit_request("Your name?")})
        return f"Hello, {_answer(responses, 'name')}!"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "greet", {})
        key = await _park_key(mcp, created.task_id)
        await update_task(
            mcp,
            created.task_id,
            {key: {"action": "accept", "content": {"value": "Ada"}}},
        )
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": "Hello, Ada!"}


async def test_guard_return_multiple_rounds_use_distinct_keys():
    """A tool that asks twice surfaces distinct keys across rounds (SEP-2663 L350).

    The second round's key must differ from the first's — a client that
    deduplicates by key must not suppress the second ask. Cross-round state
    travels through `request_state` (each leg's `input_responses` holds only
    that leg's answers, matching the foreground guard contract).
    """
    mcp = FastMCP("guard")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def full_name(ctx: Context) -> str | mcp_types.InputRequiredResult:
        responses = ctx.input_responses
        if responses is None:
            # Round 1: ask for the first name.
            return _input_required({"first": _elicit_request("First name?")})
        if ctx.request_state is None:
            # Round 2: carry the first name forward in request_state, ask last.
            return _input_required(
                {"last": _elicit_request("Last name?")},
                request_state=_answer(responses, "first"),
            )
        # Round 3: request_state holds the first name; responses holds the last.
        return f"{ctx.request_state} {_answer(responses, 'last')}"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "full_name", {})
        key1 = await _park_key(mcp, created.task_id)
        await update_task(
            mcp,
            created.task_id,
            {key1: {"action": "accept", "content": {"value": "Ada"}}},
        )
        key2 = await _park_key(mcp, created.task_id)
        assert key2 != key1
        await update_task(
            mcp,
            created.task_id,
            {key2: {"action": "accept", "content": {"value": "Lovelace"}}},
        )
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": "Ada Lovelace"}


async def test_non_guard_tool_runs_once():
    """A tool that never asks for input completes in a single invocation."""
    calls: list[int] = []
    mcp = FastMCP("guard")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def square(n: int) -> int:
        calls.append(n)
        return n * n

    async with running_task_server(mcp):
        created = await submit_task(mcp, "square", {"n": 6})
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 36}
    assert calls == [6]


def test_reentrant_wrapper_preserves_signature():
    """The wrapper keeps the tool's parameters so Docket DI is unchanged."""
    import inspect

    from fastmcp_tasks.input_loop import reentrant_task_fn

    async def fn(n: int, ctx: Any) -> int:
        return n

    wrapped = reentrant_task_fn(fn, "fn")
    assert list(inspect.signature(wrapped).parameters) == ["n", "ctx"]


async def test_state_only_guard_round_fails_clearly():
    """A state-only guard round (request_state, no input_requests) fails loudly.

    Foreground, the client re-invokes such a round after a backoff. The tasked
    path has no self-continuation for it, so rather than silently completing with
    a wrong result it surfaces an actionable error.
    """
    mcp = FastMCP("guard-state-only")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def checkpoint(ctx: Context) -> str | mcp_types.InputRequiredResult:
        if ctx.request_state is None:
            return _input_required({}, request_state="carried")
        return "done"

    async with running_task_server(mcp):
        final = await wait_for_task(
            mcp, (await submit_task(mcp, "checkpoint", {})).task_id
        )

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["isError"] is True
    assert "state-only" in final.result["content"][0]["text"]
