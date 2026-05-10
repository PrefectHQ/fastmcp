"""Tests for FastMCP Progress dependency."""

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.dependencies import Progress


async def test_progress_in_immediate_execution():
    """Test Progress dependency when calling tool immediately with Docket enabled."""
    mcp = FastMCP("test")

    @mcp.tool()
    async def test_tool(progress: Progress = Progress()) -> str:
        await progress.set_total(10)
        await progress.increment()
        await progress.set_message("Testing")
        return "done"

    async with Client(mcp) as client:
        result = await client.call_tool("test_tool", {})
        from mcp.types import TextContent

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "done"


async def test_progress_in_background_task():
    """Test Progress dependency in background task execution."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def test_task(progress: Progress = Progress()) -> str:
        await progress.set_total(5)
        await progress.increment()
        await progress.set_message("Step 1")
        return "done"

    async with Client(mcp) as client:
        task = await client.call_tool("test_task", {}, task=True)
        result = await task.result()
        from mcp.types import TextContent

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "done"


async def test_progress_tracks_multiple_increments():
    """Test that Progress correctly tracks multiple increment calls."""
    mcp = FastMCP("test")

    @mcp.tool()
    async def count_to_ten(progress: Progress = Progress()) -> str:
        await progress.set_total(10)
        for i in range(10):
            await progress.increment()
        return "counted"

    async with Client(mcp) as client:
        result = await client.call_tool("count_to_ten", {})
        from mcp.types import TextContent

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "counted"


async def test_progress_status_message_in_background_task():
    """Regression test: TaskStatusResponse must include statusMessage field."""
    import asyncio

    mcp = FastMCP("test")
    step_started = asyncio.Event()

    @mcp.tool(task=True)
    async def task_with_progress(progress: Progress = Progress()) -> str:
        await progress.set_total(3)
        await progress.set_message("Step 1 of 3")
        await progress.increment()
        step_started.set()

        # Give test time to poll status
        await asyncio.sleep(0.2)

        await progress.set_message("Step 2 of 3")
        await progress.increment()
        await progress.set_message("Step 3 of 3")
        await progress.increment()
        return "done"

    async with Client(mcp) as client:
        task = await client.call_tool("task_with_progress", {}, task=True)

        # Wait for first step to start
        await step_started.wait()

        # Get status and verify progress message
        status = await task.status()

        # Verify statusMessage field is accessible and contains progress info
        # Should not raise AttributeError
        msg = status.statusMessage
        assert msg is None or msg.startswith("Step")

        # Wait for completion
        result = await task.result()
        from mcp.types import TextContent

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "done"


async def test_inmemory_progress_state():
    """Test that in-memory progress stores and returns state correctly."""
    mcp = FastMCP("test")

    @mcp.tool()
    async def test_tool(progress: Progress = Progress()) -> dict:
        # Initial state
        assert progress.current is None
        assert progress.total == 1
        assert progress.message is None

        # Set total
        await progress.set_total(10)
        assert progress.total == 10

        # Increment
        await progress.increment()
        assert progress.current == 1

        # Increment again
        await progress.increment(2)
        assert progress.current == 3

        # Set message
        await progress.set_message("Testing")
        assert progress.message == "Testing"

        return {
            "current": progress.current,
            "total": progress.total,
            "message": progress.message,
        }

    async with Client(mcp) as client:
        result = await client.call_tool("test_tool", {})
        from mcp.types import TextContent

        assert isinstance(result.content[0], TextContent)
        # The tool returns a dict showing the final state
        import json

        state = json.loads(result.content[0].text)
        assert state["current"] == 3
        assert state["total"] == 10
        assert state["message"] == "Testing"
