"""Tests for dependency injection in background tasks.

These tests verify that Docket's dependency system works correctly when
user functions are queued as background tasks. Dependencies like CurrentDocket(),
CurrentFastMCP(), and Depends() should be resolved in the worker context.
"""

from contextlib import asynccontextmanager
from typing import Any, cast

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.dependencies import CurrentDocket, CurrentFastMCP, Depends


@pytest.fixture
async def dependency_server():
    """Create a FastMCP server with dependency-using background tasks."""
    mcp = FastMCP("dependency-test-server")

    # Track dependency injection
    injected_values = []

    @mcp.tool(task=True)
    async def tool_with_docket_dependency(docket=CurrentDocket()) -> str:
        """Background tool that uses CurrentDocket dependency."""
        injected_values.append(("docket", docket))
        return f"Docket: {docket is not None}"

    @mcp.tool(task=True)
    async def tool_with_server_dependency(server=CurrentFastMCP()) -> str:
        """Background tool that uses CurrentFastMCP dependency."""
        injected_values.append(("server", server))
        return f"Server: {server.name}"

    @mcp.tool(task=True)
    async def tool_with_custom_dependency(
        value: int, multiplier: int = Depends(lambda: 10)
    ) -> int:
        """Background tool with custom Depends()."""
        injected_values.append(("multiplier", multiplier))
        return value * multiplier

    @mcp.tool(task=True)
    async def tool_with_multiple_dependencies(
        name: str,
        docket=CurrentDocket(),
        server=CurrentFastMCP(),
    ) -> str:
        """Background tool with multiple dependencies."""
        injected_values.append(("multi_docket", docket))
        injected_values.append(("multi_server", server))
        return f"{name} on {server.name}"

    @mcp.prompt(task=True)
    async def prompt_with_server_dependency(topic: str, server=CurrentFastMCP()) -> str:
        """Background prompt that uses CurrentFastMCP dependency."""
        injected_values.append(("prompt_server", server))
        return f"Prompt from {server.name} about {topic}"

    @mcp.resource("file://data.txt", task=True)
    async def resource_with_docket_dependency(docket=CurrentDocket()) -> str:
        """Background resource that uses CurrentDocket dependency."""
        injected_values.append(("resource_docket", docket))
        return f"Resource via Docket: {docket is not None}"

    # Expose for test assertions
    mcp._injected_values = injected_values  # type: ignore[attr-defined]

    return mcp


async def test_background_tool_receives_docket_dependency(dependency_server):
    """Background tools can use CurrentDocket() and it resolves correctly."""
    async with Client(dependency_server) as client:
        task = await client.call_tool("tool_with_docket_dependency", {}, task=True)

        # Verify it's background
        assert not task.returned_immediately

        # Get result - will execute in Docket worker
        result = await task

        # Verify dependency was injected
        assert len(dependency_server._injected_values) == 1
        dep_type, dep_value = dependency_server._injected_values[0]
        assert dep_type == "docket"
        assert dep_value is not None
        assert "Docket: True" in result.data


async def test_background_tool_receives_server_dependency(dependency_server):
    """Background tools can use CurrentFastMCP() and get the actual FastMCP server."""
    dependency_server._injected_values.clear()

    async with Client(dependency_server) as client:
        task = await client.call_tool("tool_with_server_dependency", {}, task=True)

        # Verify background execution
        assert not task.returned_immediately

        result = await task

        # Check the server instance was injected
        assert len(dependency_server._injected_values) == 1
        dep_type, dep_value = dependency_server._injected_values[0]
        assert dep_type == "server"
        assert dep_value is dependency_server  # Same instance!
        assert f"Server: {dependency_server.name}" in result.data


async def test_background_tool_receives_custom_depends(dependency_server):
    """Background tools can use Depends() with custom functions."""
    dependency_server._injected_values.clear()

    async with Client(dependency_server) as client:
        task = await client.call_tool(
            "tool_with_custom_dependency", {"value": 5}, task=True
        )

        assert not task.returned_immediately

        result = await task

        # Check dependency was resolved
        assert len(dependency_server._injected_values) == 1
        dep_type, dep_value = dependency_server._injected_values[0]
        assert dep_type == "multiplier"
        assert dep_value == 10
        assert result.data == 50  # 5 * 10


async def test_background_tool_with_multiple_dependencies(dependency_server):
    """Background tools can have multiple dependencies injected simultaneously."""
    dependency_server._injected_values.clear()

    async with Client(dependency_server) as client:
        task = await client.call_tool(
            "tool_with_multiple_dependencies", {"name": "test"}, task=True
        )

        assert not task.returned_immediately

        await task

        # Both dependencies should be injected
        assert len(dependency_server._injected_values) == 2

        dep_types = {item[0] for item in dependency_server._injected_values}
        assert "multi_docket" in dep_types
        assert "multi_server" in dep_types

        # Verify values
        server_dep = next(
            v for t, v in dependency_server._injected_values if t == "multi_server"
        )
        assert server_dep is dependency_server


async def test_background_prompt_receives_dependencies(dependency_server):
    """Background prompts can use dependency injection."""
    dependency_server._injected_values.clear()

    async with Client(dependency_server) as client:
        task = await client.get_prompt(
            "prompt_with_server_dependency", {"topic": "AI"}, task=True
        )

        assert not task.returned_immediately

        await task

        # Check dependency was injected
        assert len(dependency_server._injected_values) == 1
        dep_type, dep_value = dependency_server._injected_values[0]
        assert dep_type == "prompt_server"
        assert dep_value is dependency_server


async def test_background_resource_receives_dependencies(dependency_server):
    """Background resources can use dependency injection."""
    dependency_server._injected_values.clear()

    async with Client(dependency_server) as client:
        task = await client.read_resource("file://data.txt", task=True)

        assert not task.returned_immediately

        await task

        # Check dependency was injected
        assert len(dependency_server._injected_values) == 1
        dep_type, dep_value = dependency_server._injected_values[0]
        assert dep_type == "resource_docket"
        assert dep_value is not None


async def test_foreground_tool_dependencies_unaffected(dependency_server):
    """Synchronous tools (task=False) still get dependencies as before."""
    dependency_server._injected_values.clear()

    @dependency_server.tool()  # task=False
    async def sync_tool(server=CurrentFastMCP()) -> str:
        dependency_server._injected_values.append(("sync_server", server))
        return f"Sync: {server.name}"

    async with Client(dependency_server) as client:
        await client.call_tool("sync_tool", {})

        # Should execute immediately
        assert len(dependency_server._injected_values) == 1
        assert dependency_server._injected_values[0][1] is dependency_server


async def test_dependency_context_managers_cleaned_up_in_background():
    """Context manager dependencies are properly cleaned up after background task."""
    cleanup_called = []

    mcp = FastMCP("cleanup-test")

    @asynccontextmanager
    async def tracked_connection():
        try:
            cleanup_called.append("enter")
            yield "connection"
        finally:
            cleanup_called.append("exit")

    @mcp.tool(task=True)
    async def use_connection(name: str, conn: str = Depends(tracked_connection)) -> str:  # type: ignore[assignment]
        assert conn == "connection"
        assert "enter" in cleanup_called
        assert "exit" not in cleanup_called  # Still open during execution
        return f"Used: {conn}"

    async with Client(mcp) as client:
        task = await client.call_tool("use_connection", {"name": "test"}, task=True)
        result = await task

        # After task completes, cleanup should have been called
        assert cleanup_called == ["enter", "exit"]
        assert "Used: connection" in result.data


async def test_dependency_errors_propagate_to_task_failure():
    """If dependency resolution fails, the background task should fail."""
    mcp = FastMCP("error-test")

    async def failing_dependency():
        raise ValueError("Dependency failed!")

    @mcp.tool(task=True)
    async def tool_with_failing_dep(
        value: str, dep: str = cast(Any, Depends(failing_dependency))
    ) -> str:
        return f"Got: {dep}"

    from fastmcp.exceptions import ToolError

    async with Client(mcp) as client:
        task = await client.call_tool(
            "tool_with_failing_dep", {"value": "test"}, task=True
        )

        # Task should fail due to dependency error
        with pytest.raises(ToolError, match="Failed to resolve dependencies"):
            await task.result()

        # Verify it reached failed state
        status = await task.status()
        assert status.status == "failed"
