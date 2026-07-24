"""Tests for dependency injection in background tasks.

These tests verify that Docket's dependency system works correctly when tool
functions are queued as background tasks. Dependencies like CurrentDocket(),
CurrentFastMCP(), and Depends() should be resolved in the worker context.

SEP-2663 is tools-only, so only tools carry a task-capable config; the removed
prompt/resource task cases are gone.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from fastmcp_tasks.dependencies import CurrentDocket
from uncalled_for import Depends

from fastmcp import Context, FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import CurrentFastMCP
from fastmcp.server.sessions import UserSession
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    call_tool_without_optin,
    run_task,
    running_task_server,
)


@pytest.fixture
def dependency_server() -> FastMCP:
    """A FastMCP server with dependency-using background tools."""
    mcp = FastMCP("dependency-test-server")
    mcp.add_extension(TasksExtension())

    injected_values: list[tuple[str, Any]] = []

    @mcp.tool(task=True)
    async def tool_with_docket_dependency(docket=CurrentDocket()) -> str:
        injected_values.append(("docket", docket))
        return f"Docket: {docket is not None}"

    @mcp.tool(task=True)
    async def tool_with_server_dependency(server=CurrentFastMCP()) -> str:
        injected_values.append(("server", server))
        return f"Server: {server.name}"

    @mcp.tool(task=True)
    async def tool_with_custom_dependency(
        value: int, multiplier: int = Depends(lambda: 10)
    ) -> int:
        injected_values.append(("multiplier", multiplier))
        return value * multiplier

    @mcp.tool(task=True)
    async def tool_with_multiple_dependencies(
        name: str,
        docket=CurrentDocket(),
        server=CurrentFastMCP(),
    ) -> str:
        injected_values.append(("multi_docket", docket))
        injected_values.append(("multi_server", server))
        return f"{name} on {server.name}"

    mcp._injected_values = injected_values  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]

    return mcp


async def test_background_tool_receives_docket_dependency(dependency_server):
    """Background tools can use CurrentDocket() and it resolves in the worker."""
    async with running_task_server(dependency_server):
        final = await run_task(dependency_server, "tool_with_docket_dependency", {})

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": "Docket: True"}
    assert len(dependency_server._injected_values) == 1
    dep_type, dep_value = dependency_server._injected_values[0]
    assert dep_type == "docket"
    assert dep_value is not None


async def test_background_tool_receives_server_dependency(dependency_server):
    """Background tools can use CurrentFastMCP() and get the actual server."""
    dependency_server._injected_values.clear()

    async with running_task_server(dependency_server):
        final = await run_task(dependency_server, "tool_with_server_dependency", {})

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {
        "result": f"Server: {dependency_server.name}"
    }
    assert len(dependency_server._injected_values) == 1
    dep_type, dep_value = dependency_server._injected_values[0]
    assert dep_type == "server"
    assert dep_value is dependency_server  # Same instance!


async def test_background_tool_receives_custom_depends(dependency_server):
    """Background tools can use Depends() with custom functions."""
    dependency_server._injected_values.clear()

    async with running_task_server(dependency_server):
        final = await run_task(
            dependency_server, "tool_with_custom_dependency", {"value": 5}
        )

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 50}  # 5 * 10
    assert len(dependency_server._injected_values) == 1
    dep_type, dep_value = dependency_server._injected_values[0]
    assert dep_type == "multiplier"
    assert dep_value == 10


async def test_background_tool_with_multiple_dependencies(dependency_server):
    """Background tools can have multiple dependencies injected at once."""
    dependency_server._injected_values.clear()

    async with running_task_server(dependency_server):
        final = await run_task(
            dependency_server, "tool_with_multiple_dependencies", {"name": "test"}
        )

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {
        "result": f"test on {dependency_server.name}"
    }

    dep_types = {item[0] for item in dependency_server._injected_values}
    assert "multi_docket" in dep_types
    assert "multi_server" in dep_types

    server_dep = next(
        v for t, v in dependency_server._injected_values if t == "multi_server"
    )
    assert server_dep is dependency_server


async def test_foreground_tool_dependencies_unaffected(dependency_server):
    """Synchronous tools still get their dependencies as before."""
    dependency_server._injected_values.clear()

    @dependency_server.tool
    async def sync_tool(server=CurrentFastMCP()) -> str:
        dependency_server._injected_values.append(("sync_server", server))
        return f"Sync: {server.name}"

    async with running_task_server(dependency_server):
        await call_tool_without_optin(dependency_server, "sync_tool", {})

    assert len(dependency_server._injected_values) == 1
    assert dependency_server._injected_values[0][1] is dependency_server


async def test_dependency_context_managers_cleaned_up_in_background():
    """Context-manager dependencies are cleaned up after a background task."""
    cleanup_called: list[str] = []

    mcp = FastMCP("cleanup-test")
    mcp.add_extension(TasksExtension())

    @asynccontextmanager
    async def tracked_connection():
        try:
            cleanup_called.append("enter")
            yield "connection"
        finally:
            cleanup_called.append("exit")

    @mcp.tool(task=True)
    async def use_connection(name: str, conn: str = Depends(tracked_connection)) -> str:
        assert conn == "connection"
        assert "enter" in cleanup_called
        assert "exit" not in cleanup_called  # Still open during execution
        return f"Used: {conn}"

    async with running_task_server(mcp):
        final = await run_task(mcp, "use_connection", {"name": "test"})

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": "Used: connection"}
    assert cleanup_called == ["enter", "exit"]


async def test_dependency_errors_propagate_to_task_failure():
    """If dependency resolution fails, the background task should fail."""
    mcp = FastMCP("error-test")
    mcp.add_extension(TasksExtension())

    async def failing_dependency():
        raise ValueError("Dependency failed!")

    @mcp.tool(task=True)
    async def tool_with_failing_dep(
        value: str, dep: str = cast(Any, Depends(failing_dependency))
    ) -> str:
        return f"Got: {dep}"

    async with running_task_server(mcp):
        final = await run_task(mcp, "tool_with_failing_dep", {"value": "test"})

    assert final.status == "failed"
    assert final.error is not None


async def test_user_session_state_persists_across_task_calls():
    """`session: UserSession` resolves in a worker and shares state per principal.

    A `UserSession` parameter is injected the same way in a background task as on
    a foreground call: it resolves through the task-aware `get_server()` and the
    authenticated principal restored from the task snapshot, with no live session
    needed. Two tasked calls under one principal therefore share a state bucket.
    """
    mcp = FastMCP("session-task")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def remember(fact: str, session: UserSession) -> list[str]:
        facts = await session.get("facts", default=[])
        facts.append(fact)
        await session.set("facts", facts)
        return facts

    alice = AccessToken(token="a", client_id="alice", scopes=[], claims={"sub": "u1"})
    bob = AccessToken(token="b", client_id="bob", scopes=[], claims={"sub": "u2"})

    async with running_task_server(mcp):
        first = await run_task(mcp, "remember", {"fact": "apples"}, access_token=alice)
        second = await run_task(mcp, "remember", {"fact": "pears"}, access_token=alice)
        other = await run_task(mcp, "remember", {"fact": "figs"}, access_token=bob)

    assert first.result is not None
    assert second.result is not None
    assert other.result is not None
    assert first.result["structuredContent"]["result"] == ["apples"]
    # Alice's second call sees her first call's state.
    assert second.result["structuredContent"]["result"] == ["apples", "pears"]
    # Bob is a distinct principal — isolated bucket.
    assert other.result["structuredContent"]["result"] == ["figs"]


async def test_ctx_session_state_works_in_background_task():
    """`ctx.session_id` and `ctx.get_state`/`set_state` work inside a worker.

    A worker has no live session, so the Context-level session API falls back to
    the stable session id captured in the task snapshot. Session-scoped state a
    task writes is therefore keyed to the submitting client and readable back.
    """
    mcp = FastMCP("ctx-session-task")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def stash(value: str, ctx: Context) -> dict[str, object]:
        await ctx.set_state("stashed", value)
        return {
            "session_id": ctx.session_id,
            "read_back": await ctx.get_state("stashed"),
        }

    async with running_task_server(mcp):
        final = await run_task(mcp, "stash", {"value": "hello"})

    assert final.status == "completed"
    assert final.result is not None
    structured = final.result["structuredContent"]
    assert structured["read_back"] == "hello"
    assert isinstance(structured["session_id"], str) and structured["session_id"]
