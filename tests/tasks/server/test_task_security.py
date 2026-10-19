"""Authorization-based task isolation (CRITICAL SECURITY).

Tasks are scoped to the caller's authorization identity via the auth-scoped
compound Docket key, so a caller can only resolve tasks it created. A cross-scope
task id is indistinguishable from a missing one (-32602 "not found"), which keeps
task existence from leaking across callers. These tests drive the task lifecycle
in-process (there is no client task API until Phase 4), binding a different
access token per caller through the shared helper.
"""

from __future__ import annotations

import pytest
from mcp.shared.exceptions import MCPError

from fastmcp import FastMCP
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    get_task,
    make_access_token,
    run_task,
    running_task_server,
    submit_task,
    wait_for_task,
)


@pytest.fixture
def task_server() -> FastMCP:
    mcp = FastMCP("security-test-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def secret_tool(data: str) -> str:
        return f"Secret result: {data}"

    return mcp


async def test_same_client_can_access_all_its_tasks(task_server: FastMCP):
    """A single authenticated caller can resolve every task it created."""
    token = make_access_token("client-a")
    async with running_task_server(task_server):
        first = await run_task(
            task_server, "secret_tool", {"data": "first"}, access_token=token
        )
        second = await run_task(
            task_server, "secret_tool", {"data": "second"}, access_token=token
        )
        assert first.result is not None
        assert "first" in first.result["content"][0]["text"]
        assert second.result is not None
        assert "second" in second.result["content"][0]["text"]


async def test_unauthenticated_client_can_access_its_tasks(task_server: FastMCP):
    """An anonymous caller can resolve tasks in the anonymous keyspace."""
    async with running_task_server(task_server):
        final = await run_task(task_server, "secret_tool", {"data": "hello"})
        assert final.result is not None
        assert "hello" in final.result["content"][0]["text"]


async def test_distinct_clients_cannot_access_each_others_tasks(
    task_server: FastMCP,
):
    """Two distinct client_ids live in disjoint scopes: a peer's id is 'not found'."""
    alice = make_access_token("client-a")
    bob = make_access_token("client-b")
    async with running_task_server(task_server):
        created = await submit_task(
            task_server, "secret_tool", {"data": "a-secret"}, access_token=alice
        )
        with pytest.raises(MCPError, match="not found"):
            await get_task(task_server, created.task_id, access_token=bob)


async def test_distinct_subs_same_client_id_cannot_access_each_others_tasks(
    task_server: FastMCP,
):
    """Fixed-OAuth case: one client_id, distinct ``sub`` claims stay isolated."""
    shared = "shared-oauth-app"
    alice = make_access_token(shared, sub="user-alice")
    bob = make_access_token(shared, sub="user-bob")
    async with running_task_server(task_server):
        created = await submit_task(
            task_server, "secret_tool", {"data": "alice-secret"}, access_token=alice
        )
        with pytest.raises(MCPError, match="not found"):
            await get_task(task_server, created.task_id, access_token=bob)


async def test_authenticated_and_anonymous_keyspaces_are_disjoint(
    task_server: FastMCP,
):
    """An anonymous caller cannot read an authenticated caller's task."""
    authed = make_access_token("client-a")
    async with running_task_server(task_server):
        created = await submit_task(
            task_server, "secret_tool", {"data": "authed-secret"}, access_token=authed
        )
        # No access_token -> anonymous keyspace -> cannot resolve the authed task.
        with pytest.raises(MCPError, match="not found"):
            await get_task(task_server, created.task_id)
        # And the authenticated caller still resolves it.
        seen = await wait_for_task(task_server, created.task_id, access_token=authed)
        assert seen.status == "completed"
