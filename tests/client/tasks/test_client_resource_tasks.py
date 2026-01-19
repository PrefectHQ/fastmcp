"""
Tests for client-side resource task methods.

Tests the client's read_resource_as_task method.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.tasks import ResourceTask


@pytest.fixture
async def resource_server():
    """Create a test server with background-enabled resources."""
    mcp = FastMCP("resource-client-test")

    @mcp.resource("file://document.txt", task=True)
    async def document() -> str:
        """A document resource."""
        return "Document content here"

    @mcp.resource("file://data/{id}.json", task=True)
    async def data_file(id: str) -> str:
        """A parameterized data resource."""
        return f'{{"id": "{id}", "value": 42}}'

    return mcp


async def test_read_resource_as_task_returns_resource_task(resource_server):
    """read_resource with task=True returns a ResourceTask object."""
    async with Client(resource_server) as client:
        task = await client.read_resource("file://document.txt", task=True)

        assert isinstance(task, ResourceTask)
        assert isinstance(task.task_id, str)


async def test_resource_task_server_generated_id(resource_server):
    """read_resource with task=True gets server-generated task ID."""
    async with Client(resource_server) as client:
        task = await client.read_resource("file://document.txt", task=True)

        # Server should generate a UUID task ID
        assert task.task_id is not None
        assert isinstance(task.task_id, str)
        # UUIDs have hyphens
        assert "-" in task.task_id


async def test_resource_task_result_returns_read_resource_result(resource_server):
    """ResourceTask.result() returns list of ReadResourceContents."""
    async with Client(resource_server) as client:
        task = await client.read_resource("file://document.txt", task=True)

        # Verify background execution
        assert not task.returned_immediately

        # Get result
        result = await task.result()

        # Result should be list of ReadResourceContents
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].text == "Document content here"


async def test_resource_task_await_syntax(resource_server):
    """ResourceTask can be awaited directly."""
    async with Client(resource_server) as client:
        task = await client.read_resource("file://document.txt", task=True)

        # Can await task directly
        result = await task
        assert result[0].text == "Document content here"


async def test_resource_template_task(resource_server):
    """Resource templates work with task support."""
    async with Client(resource_server) as client:
        task = await client.read_resource("file://data/999.json", task=True)

        # Verify background execution
        assert not task.returned_immediately

        # Get result
        result = await task.result()
        assert '"id": "999"' in result[0].text


async def test_resource_task_status_and_wait(resource_server):
    """ResourceTask supports status() and wait() methods."""
    async with Client(resource_server) as client:
        task = await client.read_resource("file://document.txt", task=True)

        # Check status
        status = await task.status()
        assert status.status in ["working", "completed"]

        # Wait for completion
        await task.wait(timeout=2.0)

        # Get result
        result = await task.result()
        assert "Document content" in result[0].text
