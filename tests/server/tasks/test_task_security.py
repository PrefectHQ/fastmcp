"""
Tests for authorization-based task isolation (CRITICAL SECURITY).

Ensures that tasks are properly scoped to authorization identity and clients
cannot access each other's tasks.
"""

import pytest
from mcp.server.auth.middleware.auth_context import (
    auth_context_var,
)
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.auth import AccessToken


@pytest.fixture
def task_server():
    """Create a server with background tasks enabled."""
    mcp = FastMCP("security-test-server")

    @mcp.tool(task=True)
    async def secret_tool(data: str) -> str:
        """A tool that processes sensitive data."""
        return f"Secret result: {data}"

    return mcp


async def test_same_client_can_access_all_its_tasks(task_server: FastMCP):
    """A single authenticated client can access all tasks it created."""
    token = AccessToken(
        token="token-a",
        client_id="client-a",
        scopes=["read"],
    )
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        async with Client(task_server) as client:
            task1 = await client.call_tool(
                "secret_tool", {"data": "first"}, task=True, task_id="task-1"
            )
            task2 = await client.call_tool(
                "secret_tool", {"data": "second"}, task=True, task_id="task-2"
            )

            await task1.wait(timeout=2.0)
            await task2.wait(timeout=2.0)

            result1 = await task1.result()
            result2 = await task2.result()

            assert "first" in str(result1.data)
            assert "second" in str(result2.data)
    finally:
        auth_context_var.reset(reset)


async def test_unauthenticated_client_can_access_its_tasks(task_server: FastMCP):
    """An unauthenticated client can access tasks it created (by task ID)."""
    async with Client(task_server) as client:
        task = await client.call_tool(
            "secret_tool", {"data": "hello"}, task=True, task_id="my-task"
        )
        await task.wait(timeout=2.0)
        result = await task.result()
        assert "hello" in str(result.data)


def _set_auth(client_id: str, sub: str | None = None):
    """Install an auth context for a given client_id/sub. Returns the reset token."""
    claims = {"sub": sub} if sub else {}
    token = AccessToken(
        token=f"token-{client_id}-{sub or ''}",
        client_id=client_id,
        scopes=["read"],
        claims=claims,
    )
    return auth_context_var.set(AuthenticatedUser(token))


async def _submit_task_id(client: Client, data: str) -> str:
    """Submit a background task and return its server-assigned task id."""
    task = await client.call_tool("secret_tool", {"data": data}, task=True)
    await task.wait(timeout=2.0)
    return task.task_id


async def test_distinct_clients_cannot_access_each_others_tasks(
    task_server: FastMCP,
):
    """Two distinct authenticated clients live in disjoint scopes — looking up
    a peer's task id returns 'not found'."""
    reset = _set_auth("client-a")
    try:
        async with Client(task_server) as client_a:
            task_id = await _submit_task_id(client_a, "client-a-secret")
    finally:
        auth_context_var.reset(reset)

    reset = _set_auth("client-b")
    try:
        async with Client(task_server) as client_b:
            with pytest.raises(Exception, match="not found"):
                await client_b.get_task_status(task_id)
    finally:
        auth_context_var.reset(reset)


async def test_distinct_subs_same_client_id_cannot_access_each_others_tasks(
    task_server: FastMCP,
):
    """Fixed-OAuth case: two users share a client_id but have distinct ``sub``
    claims.  The ``sub``-aware scope must still isolate them."""
    shared_client = "shared-oauth-app"

    reset = _set_auth(shared_client, sub="user-alice")
    try:
        async with Client(task_server) as alice:
            task_id = await _submit_task_id(alice, "alice-secret")
    finally:
        auth_context_var.reset(reset)

    reset = _set_auth(shared_client, sub="user-bob")
    try:
        async with Client(task_server) as bob:
            with pytest.raises(Exception, match="not found"):
                await bob.get_task_status(task_id)
    finally:
        auth_context_var.reset(reset)


async def test_authenticated_and_anonymous_keyspaces_are_disjoint(
    task_server: FastMCP,
):
    """An anonymous client must not be able to read an authenticated client's
    tasks (and vice versa) even when colliding on task id."""
    reset = _set_auth("client-a")
    try:
        async with Client(task_server) as authed:
            authed_task_id = await _submit_task_id(authed, "authed-secret")
    finally:
        auth_context_var.reset(reset)

    async with Client(task_server) as anon:
        with pytest.raises(Exception, match="not found"):
            await anon.get_task_status(authed_task_id)
