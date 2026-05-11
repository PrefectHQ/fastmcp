"""
Tests for SEP-1686 background task support for resources.

Tests that resources with task=True can execute in background.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.tasks import ResourceTask


@pytest.fixture
async def resource_server():
    """Create a FastMCP server with task-enabled resources."""
    mcp = FastMCP("resource-test-server")

    @mcp.resource("file://data.txt")
    async def simple_resource() -> str:
        """A simple resource."""
        return "Simple content"

    @mcp.resource("file://large.txt", task=True)
    async def background_resource() -> str:
        """A resource that can execute in background."""
        return "Large file content that takes time to load"

    @mcp.resource("file://user/{user_id}/data.json", task=True)
    async def template_resource(user_id: str) -> str:
        """A resource template that can execute in background."""
        return f'{{"userId": "{user_id}", "data": "value"}}'

    return mcp


async def test_synchronous_resource_unchanged(resource_server):
    """Resources without task metadata execute synchronously as before."""
    async with Client(resource_server) as client:
        # Regular call without task metadata
        result = await client.read_resource("file://data.txt")

        # Should execute immediately and return result
        assert "Simple content" in str(result)


async def test_resource_with_task_metadata_returns_immediately(resource_server):
    """Resources with task metadata return immediately with ResourceTask object."""
    async with Client(resource_server) as client:
        # Call with task metadata
        task = await client.read_resource("file://large.txt", task=True)

        # Should return a ResourceTask object immediately
        assert isinstance(task, ResourceTask)
        assert isinstance(task.task_id, str)
        assert len(task.task_id) > 0


async def test_resource_task_executes_in_background(resource_server):
    """Resource task executes via Docket in background."""
    async with Client(resource_server) as client:
        task = await client.read_resource("file://large.txt", task=True)

        # Verify background execution
        assert not task.returned_immediately

        # Get the result
        result = await task.result()
        assert len(result) > 0
        assert result[0].text == "Large file content that takes time to load"


async def test_resource_template_with_task(resource_server):
    """Resource templates with task=True execute in background."""
    async with Client(resource_server) as client:
        task = await client.read_resource("file://user/123/data.json", task=True)

        # Verify background execution
        assert not task.returned_immediately

        # Get the result
        result = await task.result()
        assert '"userId": "123"' in result[0].text


async def test_forbidden_mode_resource_rejects_task_calls(resource_server):
    """Resources with task=False (mode=forbidden) reject task-augmented calls."""
    import pytest
    from mcp.shared.exceptions import McpError
    from mcp.types import METHOD_NOT_FOUND

    @resource_server.resource(
        "file://sync.txt/", task=False
    )  # Explicitly disable task support
    async def sync_only_resource() -> str:
        return "Sync content"

    async with Client(resource_server) as client:
        # Calling with task=True when task=False should raise McpError
        with pytest.raises(McpError) as exc_info:
            await client.read_resource("file://sync.txt", task=True)

        # New behavior: mode="forbidden" returns METHOD_NOT_FOUND error
        assert exc_info.value.error.code == METHOD_NOT_FOUND
        assert (
            "does not support task-augmented execution" in exc_info.value.error.message
        )
