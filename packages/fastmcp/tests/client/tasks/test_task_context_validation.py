"""
Tests for Task client context validation.

Verifies that Task methods properly validate client context and that
cached results remain accessible outside context.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client


@pytest.fixture
async def task_server():
    """Create a test server with background tasks."""
    mcp = FastMCP("context-test-server")

    @mcp.tool(task=True)
    async def background_tool(value: str) -> str:
        """Tool that runs in background."""
        return f"Result: {value}"

    @mcp.prompt(task=True)
    async def background_prompt(topic: str) -> str:
        """Prompt that runs in background."""
        return f"Prompt about {topic}"

    @mcp.resource("file://background.txt", task=True)
    async def background_resource() -> str:
        """Resource that runs in background."""
        return "Background resource content"

    return mcp


async def test_task_status_outside_context_raises(task_server):
    """Calling task.status() outside client context raises error."""
    task = None
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)
        assert not task.returned_immediately
    # Now outside context

    with pytest.raises(RuntimeError, match="outside client context"):
        await task.status()


async def test_task_result_outside_context_raises(task_server):
    """Calling task.result() outside context raises error."""
    task = None
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)
        assert not task.returned_immediately
    # Now outside context

    with pytest.raises(RuntimeError, match="outside client context"):
        await task.result()


async def test_task_wait_outside_context_raises(task_server):
    """Calling task.wait() outside context raises error."""
    task = None
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)
        assert not task.returned_immediately
    # Now outside context

    with pytest.raises(RuntimeError, match="outside client context"):
        await task.wait()


async def test_task_cancel_outside_context_raises(task_server):
    """Calling task.cancel() outside context raises error."""
    task = None
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)
        assert not task.returned_immediately
    # Now outside context

    with pytest.raises(RuntimeError, match="outside client context"):
        await task.cancel()


async def test_cached_tool_task_accessible_outside_context(task_server):
    """Tool tasks with cached results work outside context."""
    task = None
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)
        assert not task.returned_immediately

        # Get result once to cache it
        result1 = await task.result()
        assert result1.data == "Result: test"
    # Now outside context

    # Should work because result is cached
    result2 = await task.result()
    assert result2 is result1  # Same object
    assert result2.data == "Result: test"


async def test_cached_prompt_task_accessible_outside_context(task_server):
    """Prompt tasks with cached results work outside context."""
    task = None
    async with Client(task_server) as client:
        task = await client.get_prompt(
            "background_prompt", {"topic": "test"}, task=True
        )
        assert not task.returned_immediately

        # Get result once to cache it
        result1 = await task.result()
        assert result1.description == "Prompt that runs in background."
    # Now outside context

    # Should work because result is cached
    result2 = await task.result()
    assert result2 is result1  # Same object
    assert result2.description == "Prompt that runs in background."


async def test_cached_resource_task_accessible_outside_context(task_server):
    """Resource tasks with cached results work outside context."""
    task = None
    async with Client(task_server) as client:
        task = await client.read_resource("file://background.txt", task=True)
        assert not task.returned_immediately

        # Get result once to cache it
        result1 = await task.result()
        assert len(result1) > 0
    # Now outside context

    # Should work because result is cached
    result2 = await task.result()
    assert result2 is result1  # Same object


async def test_uncached_status_outside_context_raises(task_server):
    """Even after caching result, status() still requires client context."""
    task = None
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)
        assert not task.returned_immediately

        # Cache the result
        await task.result()
    # Now outside context

    # result() works (cached)
    result = await task.result()
    assert result.data == "Result: test"

    # But status() still needs client connection
    with pytest.raises(RuntimeError, match="outside client context"):
        await task.status()


async def test_task_await_syntax_outside_context_raises(task_server):
    """Using await task syntax outside context raises error for background tasks."""
    task = None
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)
        assert not task.returned_immediately
    # Now outside context

    with pytest.raises(RuntimeError, match="outside client context"):
        await task  # Same as await task.result()


async def test_task_await_syntax_works_for_cached_results(task_server):
    """Using await task syntax works outside context when result is cached."""
    task = None
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)
        result1 = await task  # Cache it
    # Now outside context

    result2 = await task  # Should work (cached)
    assert result2 is result1
    assert result2.data == "Result: test"


async def test_multiple_result_calls_return_same_cached_object(task_server):
    """Multiple result() calls return the same cached object."""
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)

        result1 = await task.result()
        result2 = await task.result()
        result3 = await task.result()

        # Should all be the same object (cached)
        assert result1 is result2
        assert result2 is result3


async def test_background_task_properties_accessible_outside_context(task_server):
    """Background task properties like task_id accessible outside context."""
    task = None
    async with Client(task_server) as client:
        task = await client.call_tool("background_tool", {"value": "test"}, task=True)
        task_id_inside = task.task_id
        assert not task.returned_immediately
    # Now outside context

    # Properties should still be accessible (they don't need client connection)
    assert task.task_id == task_id_inside
    assert task.returned_immediately is False
