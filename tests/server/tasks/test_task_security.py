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
