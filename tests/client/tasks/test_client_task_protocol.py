"""
Tests for client-side task protocol.

Generic protocol tests that use tools as test fixtures.
"""

import asyncio

from fastmcp import FastMCP
from fastmcp.client import Client


async def test_end_to_end_task_flow():
    """Complete end-to-end flow: submit, poll, retrieve."""
    start_signal = asyncio.Event()
    complete_signal = asyncio.Event()

    mcp = FastMCP("protocol-test")

    @mcp.tool(task=True)
    async def controlled_tool(message: str) -> str:
        """Tool with controlled execution."""
        start_signal.set()
        await complete_signal.wait()
        return f"Processed: {message}"

    async with Client(mcp) as client:
        # Submit task
        task = await client.call_tool(
            "controlled_tool", {"message": "integration test"}, task=True
        )

        # Wait for execution to start
        await asyncio.wait_for(start_signal.wait(), timeout=2.0)

        # Check status while running
        status = await task.status()
        assert status.status in ["working"]

        # Signal completion
        complete_signal.set()

        # Wait for task to finish and retrieve result
        result = await task.result()
        assert result.data == "Processed: integration test"


async def test_multiple_concurrent_tasks():
    """Multiple tasks can run concurrently."""
    mcp = FastMCP("concurrent-test")

    @mcp.tool(task=True)
    async def multiply(a: int, b: int) -> int:
        return a * b

    async with Client(mcp) as client:
        # Submit multiple tasks
        tasks = []
        for i in range(5):
            task = await client.call_tool("multiply", {"a": i, "b": 2}, task=True)
            tasks.append((task, i * 2))

        # Wait for all to complete and verify results
        for task, expected in tasks:
            result = await task.result()
            assert result.data == expected


async def test_task_id_auto_generation():
    """Task IDs are auto-generated if not provided."""
    mcp = FastMCP("id-test")

    @mcp.tool(task=True)
    async def echo(message: str) -> str:
        return f"Echo: {message}"

    async with Client(mcp) as client:
        # Submit without custom task ID
        task_1 = await client.call_tool("echo", {"message": "first"}, task=True)
        task_2 = await client.call_tool("echo", {"message": "second"}, task=True)

        # Should generate different IDs
        assert task_1.task_id != task_2.task_id
        assert len(task_1.task_id) > 0
        assert len(task_2.task_id) > 0
