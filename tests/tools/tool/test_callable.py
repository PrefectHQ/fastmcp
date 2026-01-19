import asyncio
import threading

from mcp.types import TextContent

from fastmcp import Context, FastMCP
from fastmcp.tools.tool import Tool


class TestToolCallable:
    """Test tools with callable objects."""

    async def test_callable_object_sync(self):
        """Test that callable objects with sync __call__ work."""

        class MyTool:
            def __init__(self, multiplier: int):
                self.multiplier = multiplier

            def __call__(self, x: int) -> int:
                return x * self.multiplier

        tool = Tool.from_function(MyTool(3))
        result = await tool.run({"x": 5})
        assert result.content == [TextContent(type="text", text="15")]

    async def test_callable_object_async(self):
        """Test that callable objects with async __call__ work."""

        class AsyncTool:
            def __init__(self, multiplier: int):
                self.multiplier = multiplier

            async def __call__(self, x: int) -> int:
                return x * self.multiplier

        tool = Tool.from_function(AsyncTool(4))
        result = await tool.run({"x": 5})
        assert result.content == [TextContent(type="text", text="20")]


class TestSyncToolConcurrency:
    """Tests for concurrent execution of sync tools without blocking the event loop."""

    async def test_sync_tools_run_concurrently(self):
        """Test that sync tools run in threadpool and don't block each other.

        Uses a threading barrier to prove concurrent execution: all calls must
        reach the barrier simultaneously for any to proceed. If they ran
        sequentially, only one would reach the barrier and it would timeout.
        """
        num_calls = 3
        # Barrier requires all threads to arrive before any proceed
        # Short timeout since concurrent threads should arrive within milliseconds
        barrier = threading.Barrier(num_calls, timeout=0.5)

        def concurrent_tool(x: int) -> int:
            """Tool that proves concurrency via barrier synchronization."""
            # If calls run sequentially, only 1 thread reaches barrier and times out
            # If calls run concurrently, all 3 reach barrier and proceed
            barrier.wait()
            return x * 2

        tool = Tool.from_function(concurrent_tool)

        # Run concurrent calls - will raise BrokenBarrierError if not concurrent
        results = await asyncio.gather(
            tool.run({"x": 1}),
            tool.run({"x": 2}),
            tool.run({"x": 3}),
        )

        # Verify results
        assert [r.content for r in results] == [
            [TextContent(type="text", text="2")],
            [TextContent(type="text", text="4")],
            [TextContent(type="text", text="6")],
        ]

    async def test_sync_tool_with_context_runs_concurrently(self):
        """Test that sync tools with Context dependency also run concurrently."""
        num_calls = 3
        barrier = threading.Barrier(num_calls, timeout=0.5)

        mcp = FastMCP("test")

        @mcp.tool
        def ctx_tool(x: int, ctx: Context) -> str:
            """A sync tool with context that uses barrier to prove concurrency."""
            barrier.wait()
            return f"{ctx.fastmcp.name}:{x}"

        # Run concurrent calls through the server interface (which sets up Context)
        results = await asyncio.gather(
            mcp.call_tool("ctx_tool", {"x": 1}),
            mcp.call_tool("ctx_tool", {"x": 2}),
            mcp.call_tool("ctx_tool", {"x": 3}),
        )

        # Verify results
        for i, result in enumerate(results, 1):
            assert result.content == [TextContent(type="text", text=f"test:{i}")]
