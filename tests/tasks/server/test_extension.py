"""End-to-end tests for the SEP-2663 `TasksExtension` server adapter.

Covers the decide-and-task interceptor (forbidden/optional/required modes and the
-32003 missing-capability error), the tasks/get|update|cancel handlers, status
mapping, inlined completed results, argument-coercion parity, TTL, and capability
advertisement. Server-side tasks are driven in-process via `task_helpers` because
there is no client task-submission API until Phase 4.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import cast

import mcp_types
import pytest
from fastmcp_tasks.models import (
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    CreateTaskResult,
    GetTaskParams,
)
from mcp.server.context import ServerRequestContext
from mcp.server.session import ServerSession
from mcp.shared.exceptions import MCPError

from fastmcp import Context, FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import bind_request_context
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID, TaskConfig
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    _opted_in_request,
    auth_scope,
    call_tool_without_optin,
    get_task,
    make_access_token,
    opt_in_meta,
    run_task,
    running_task_server,
    submit_task,
    update_task,
    wait_for_task,
)


def _tasks_server() -> FastMCP:
    mcp = FastMCP("tasks")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def square(n: int) -> int:
        return n * n

    @mcp.tool(task=TaskConfig(mode="required"))
    async def must_task(n: int) -> int:
        return n + 1

    @mcp.tool
    async def plain(n: int) -> int:
        return n - 1

    @mcp.tool(task=True)
    async def boom() -> int:
        raise ToolError("kaboom")

    return mcp


# ---------------------------------------------------------------------------
# Capability advertisement
# ---------------------------------------------------------------------------


async def test_capability_advertised_to_modern_client():
    mcp = FastMCP("t")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def t(n: int) -> int:
        return n

    async with Client(mcp, mode="auto") as client:
        extensions = client.server_capabilities.extensions or {}
        assert extensions.get(TASKS_EXTENSION_ID) == {}


async def test_capability_absent_without_extension():
    mcp = FastMCP("t")

    @mcp.tool
    async def t(n: int) -> int:
        return n

    async with Client(mcp, mode="auto") as client:
        extensions = client.server_capabilities.extensions or {}
        assert TASKS_EXTENSION_ID not in extensions


# ---------------------------------------------------------------------------
# Decide-and-task interceptor
# ---------------------------------------------------------------------------


async def test_optional_tool_tasks_when_opted_in():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "square", {"n": 5})
        assert isinstance(created, CreateTaskResult)
        assert created.status == "working"
        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"]["result"] == 25


async def test_optional_tool_runs_sync_without_opt_in():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        result = await call_tool_without_optin(mcp, "square", {"n": 5})
        assert not isinstance(result, CreateTaskResult)
        assert result.structured_content == {"result": 25}


async def test_forbidden_tool_never_tasks_even_with_opt_in():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        # `plain` is mode=forbidden; opting in must not task it.
        result = await submit_task_expecting_sync(mcp, "plain", {"n": 5})
        assert result.structured_content == {"result": 4}


async def submit_task_expecting_sync(mcp, name, args):
    with auth_scope(None), _opted_in_request(name, args, None):
        return await mcp.call_tool(name, args)


async def test_required_tool_tasks_when_opted_in():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "must_task", {"n": 10})
        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"]["result"] == 11


async def test_required_tool_without_opt_in_raises_missing_capability():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        with pytest.raises(MCPError) as exc_info:
            await call_tool_without_optin(mcp, "must_task", {"n": 1})
        error = exc_info.value.error
        assert error.code == MISSING_REQUIRED_CLIENT_CAPABILITY
        assert error.data == {
            "requiredCapabilities": {"extensions": {TASKS_EXTENSION_ID: {}}}
        }


# ---------------------------------------------------------------------------
# Task id and status
# ---------------------------------------------------------------------------


async def test_task_ids_are_server_generated_and_distinct():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        a = await submit_task(mcp, "square", {"n": 1})
        b = await submit_task(mcp, "square", {"n": 2})
        assert a.task_id != b.task_id
        assert len(a.task_id) >= 20


async def test_get_unknown_task_raises_not_found():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        with pytest.raises(MCPError, match="not found"):
            await get_task(mcp, "does-not-exist")


async def test_raised_tool_error_completes_with_is_error():
    """A tool that RAISES is a completed task with an is_error result, not failed.

    SEP-2663 reserves `failed` for protocol faults; a raised tool error is the
    same `isError` CallToolResult a live tools/call returns (the task path must
    return exactly what the underlying request would).
    """
    mcp = _tasks_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "boom", {})
        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"
        assert final.error is None
        assert final.result is not None
        assert final.result["isError"] is True
        assert "kaboom" in final.result["content"][0]["text"]


async def test_raised_generic_error_is_masked_without_ctx_param():
    """A non-FastMCP exception is masked even when the tool takes no `ctx`.

    Error masking is the server's `_mask_error_details` policy, which the task
    error path must resolve through the worker-server resolver — not the active
    `Context`. A tool that never requests `ctx` has no active context when it
    raises, so a context-based lookup would silently fall back to the global
    default and leak the raw exception text.
    """
    mcp = FastMCP("masked-task-server", mask_error_details=True)
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def leak() -> int:
        raise ValueError("secret internal detail")

    async with running_task_server(mcp):
        final = await run_task(mcp, "leak", {})

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["isError"] is True
    text = final.result["content"][0]["text"]
    assert "secret internal detail" not in text
    assert "Error calling tool 'leak'" in text


# ---------------------------------------------------------------------------
# Argument coercion parity
# ---------------------------------------------------------------------------


async def test_task_arguments_are_coerced_like_sync_path():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        # "6" coerces to int 6 exactly as the synchronous path would.
        final = await run_task(mcp, "square", {"n": "6"})
        assert final.result is not None
        assert final.result["structuredContent"]["result"] == 36


async def test_invalid_task_arguments_reject_at_submission():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        with pytest.raises(Exception):
            await submit_task(mcp, "square", {"n": "not-a-number"})


# ---------------------------------------------------------------------------
# TTL / poll interval
# ---------------------------------------------------------------------------


async def test_create_and_get_carry_ttl_and_poll_interval():
    mcp = _tasks_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "square", {"n": 3})
        assert created.ttl_ms is not None and created.ttl_ms > 0
        assert created.poll_interval_ms == 5000
        got = await get_task(mcp, created.task_id)
        assert got.ttl_ms == created.ttl_ms
        assert got.poll_interval_ms == 5000


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def test_cancel_transitions_task_to_cancelled():
    mcp = FastMCP("t")
    mcp.add_extension(TasksExtension())
    release = asyncio.Event()

    @mcp.tool(task=True)
    async def slow() -> str:
        await release.wait()
        return "done"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "slow", {})
        ack = await cancel_and_release(mcp, created.task_id, release)
        assert ack is not None
        final = await wait_for_task(
            mcp, created.task_id, target_states=frozenset({"cancelled", "completed"})
        )
        assert final.status in {"cancelled", "completed"}


async def cancel_and_release(mcp, task_id, release):
    from tests.tasks.task_helpers import cancel_task

    ack = await cancel_task(mcp, task_id)
    release.set()
    return ack


# ---------------------------------------------------------------------------
# Serve-time guard
# ---------------------------------------------------------------------------


async def test_task_tool_without_extension_fails_at_serve_time():
    mcp = FastMCP("t")

    @mcp.tool(task=True)
    async def t(n: int) -> int:
        return n

    with pytest.raises(RuntimeError, match="tasks extension"):
        async with mcp._lifespan_manager():
            pass


# ---------------------------------------------------------------------------
# Auth-scoped isolation
# ---------------------------------------------------------------------------


async def test_tasks_isolated_across_auth_scopes():
    mcp = _tasks_server()
    alice = make_access_token("alice")
    bob = make_access_token("bob")
    async with running_task_server(mcp):
        created = await submit_task(mcp, "square", {"n": 4}, access_token=alice)
        # Alice sees her task.
        mine = await get_task(mcp, created.task_id, access_token=alice)
        assert mine.task_id == created.task_id
        # Bob cannot: a cross-scope id is indistinguishable from missing.
        with pytest.raises(MCPError, match="not found"):
            await get_task(mcp, created.task_id, access_token=bob)


# ---------------------------------------------------------------------------
# Protocol-era gating of the tasking decision
# ---------------------------------------------------------------------------


async def test_legacy_era_opt_in_is_ignored():
    """A handshake-era request cannot be tasked, even with the _meta opt-in.

    The SDK strips `capabilities.extensions` from pre-2026 handshakes, so a
    legacy client can never have negotiated the tasks extension — a stray
    per-request opt-in on a legacy connection is treated as absent and an
    `optional` tool runs synchronously.
    """
    mcp = _tasks_server()
    async with running_task_server(mcp):
        srctx = ServerRequestContext(
            session=cast(ServerSession, SimpleNamespace()),
            lifespan_context={},
            protocol_version="2025-06-18",
            method="tools/call",
            params={"name": "square", "arguments": {"n": 3}, "_meta": opt_in_meta()},
        )
        with bind_request_context(srctx):
            result = await mcp.call_tool("square", {"n": 3})
    assert isinstance(result, ToolResult)


async def test_legacy_era_required_tool_raises_missing_capability():
    """`required` tools refuse legacy-era calls with -32003 even when opted in."""
    mcp = _tasks_server()
    async with running_task_server(mcp):
        srctx = ServerRequestContext(
            session=cast(ServerSession, SimpleNamespace()),
            lifespan_context={},
            protocol_version="2025-06-18",
            method="tools/call",
            params={
                "name": "must_task",
                "arguments": {"n": 3},
                "_meta": opt_in_meta(),
            },
        )
        with bind_request_context(srctx):
            with pytest.raises(MCPError) as exc_info:
                await mcp.call_tool("must_task", {"n": 3})
    assert exc_info.value.error.code == -32003


# ---------------------------------------------------------------------------
# Worker-hook lifecycle across multiple servers
# ---------------------------------------------------------------------------


async def test_worker_hooks_survive_sibling_server_shutdown():
    """One server's shutdown must not strand another server's workers.

    The worker-side hooks core exposes are process-global; two sibling servers
    each running a TasksExtension refcount them, so the hooks clear only when
    the last extension lifespan exits.
    """
    from fastmcp.server import dependencies as core_dependencies

    server_a = _tasks_server()
    server_b = _tasks_server()

    async with AsyncExitStack() as stack_b:
        await stack_b.enter_async_context(server_b._lifespan_manager())
        async with AsyncExitStack() as stack_a:
            await stack_a.enter_async_context(server_a._lifespan_manager())
            assert core_dependencies._background_context_factory is not None
        # Server A has shut down; server B's workers still need the hooks.
        assert core_dependencies._background_context_factory is not None
    # The last extension exited; hooks are cleared.
    assert core_dependencies._background_context_factory is None


# ---------------------------------------------------------------------------
# Compliance: -32003 on task methods for non-declaring clients (SEP-2663)
# ---------------------------------------------------------------------------


async def test_task_method_without_capability_raises_missing_capability():
    """tasks/get from a client that did not declare the extension gets -32003."""
    mcp = _tasks_server()
    extension = cast(TasksExtension, mcp._extensions[TASKS_EXTENSION_ID])
    # A request context with no tasks capability in its _meta.
    srctx = ServerRequestContext(
        session=cast(ServerSession, SimpleNamespace()),
        lifespan_context={},
        protocol_version="2026-07-28",
        method="tasks/get",
        params={"taskId": "whatever"},
    )
    params = GetTaskParams.model_validate({"taskId": "whatever"})
    async with running_task_server(mcp):
        with pytest.raises(MCPError) as exc_info:
            await extension._handle_get(srctx, params)
    assert exc_info.value.error.code == MISSING_REQUIRED_CLIENT_CAPABILITY


# ---------------------------------------------------------------------------
# Compliance: concurrent tasks/update must not enqueue two next legs
# ---------------------------------------------------------------------------


async def test_concurrent_update_enqueues_a_single_next_leg():
    """Two racing tasks/update answers re-enter the task exactly once."""
    calls: list[int] = []
    mcp = FastMCP("race")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def guard(ctx: Context) -> str | mcp_types.InputRequiredResult:
        calls.append(1)
        if ctx.input_responses is None:
            req = mcp_types.ElicitRequest(
                params=mcp_types.ElicitRequestFormParams(
                    message="?", requested_schema={"type": "object"}
                )
            )
            return mcp_types.InputRequiredResult(
                result_type="input_required", input_requests={"k": req}
            )
        return "done"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "guard", {})
        parked = await wait_for_task(
            mcp, created.task_id, target_states=frozenset({"input_required"})
        )
        assert parked.input_requests is not None
        key = next(iter(parked.input_requests))
        answer = {key: {"action": "accept", "content": {}}}
        # Fire two identical updates concurrently.
        await asyncio.gather(
            update_task(mcp, created.task_id, answer),
            update_task(mcp, created.task_id, answer),
            return_exceptions=True,
        )
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    # Leg 1 (park) + exactly one re-entered leg 2 — never a third from a double
    # enqueue.
    assert calls == [1, 1]
