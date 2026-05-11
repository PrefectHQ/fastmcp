"""Tests for tool timeout functionality."""

import time

import anyio
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError


class TestToolTimeout:
    """Test tool timeout behavior."""

    async def test_no_timeout_completes_normally_async(self):
        """Tool without timeout completes normally (async)."""
        mcp = FastMCP()

        @mcp.tool
        async def quick_async_tool() -> str:
            await anyio.sleep(0.01)
            return "completed"

        result = await mcp.call_tool("quick_async_tool")
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "completed"

    async def test_no_timeout_completes_normally_sync(self):
        """Tool without timeout completes normally (sync)."""
        mcp = FastMCP()

        @mcp.tool
        def quick_sync_tool() -> str:
            time.sleep(0.01)
            return "completed"

        result = await mcp.call_tool("quick_sync_tool")
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "completed"

    async def test_timeout_not_reached_async(self):
        """Async tool with timeout completes before timeout."""
        mcp = FastMCP()

        @mcp.tool(timeout=5.0)
        async def fast_async_tool() -> str:
            await anyio.sleep(0.1)
            return "completed"

        result = await mcp.call_tool("fast_async_tool")
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "completed"

    async def test_timeout_not_reached_sync(self):
        """Sync tool with timeout completes before timeout."""
        mcp = FastMCP()

        @mcp.tool(timeout=5.0)
        def fast_sync_tool() -> str:
            time.sleep(0.1)
            return "completed"

        result = await mcp.call_tool("fast_sync_tool")
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "completed"

    async def test_async_timeout_exceeded(self):
        """Async tool exceeds timeout and raises TimeoutError."""
        mcp = FastMCP()

        @mcp.tool(timeout=0.2)
        async def slow_async_tool() -> str:
            await anyio.sleep(2.0)
            return "should not reach"

        # TimeoutError is caught and converted to ToolError by FastMCP
        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool("slow_async_tool")

        # Verify the tool raised an error (error message may be masked)
        assert exc_info.value is not None

    async def test_sync_timeout_exceeded(self):
        """Sync tool timeout works with CPU-bound operations."""
        pytest.skip(
            "Sync timeouts require thread pool execution (coming in future commit)"
        )
        # Note: time.sleep() blocks the event loop and cannot be interrupted
        # by anyio.fail_after(). This will work once sync functions run in
        # thread pools (commit 8c471a49).

    async def test_timeout_error_raises_tool_error(self):
        """Timeout error is converted to ToolError and logs warning."""
        mcp = FastMCP()

        @mcp.tool(timeout=0.1)
        async def slow_tool() -> str:
            await anyio.sleep(1.0)
            return "never"

        # Verify that ToolError is raised (timeout warning is logged to stderr)
        with pytest.raises(ToolError):
            await mcp.call_tool("slow_tool")

    async def test_timeout_from_tool_from_function(self):
        """Timeout works when using Tool.from_function()."""
        from fastmcp.tools import Tool

        async def my_slow_tool() -> str:
            await anyio.sleep(1.0)
            return "never"

        tool = Tool.from_function(my_slow_tool, timeout=0.1)
        mcp = FastMCP()
        mcp.add_tool(tool)

        with pytest.raises(ToolError):
            await mcp.call_tool("my_slow_tool")

    async def test_timeout_zero_times_out_immediately(self):
        """Timeout of 0 times out immediately."""
        mcp = FastMCP()

        @mcp.tool(timeout=0.0)
        async def instant_timeout() -> str:
            await anyio.sleep(0)  # Give the event loop a chance to check timeout
            return "never"

        with pytest.raises(ToolError):
            await mcp.call_tool("instant_timeout")

    async def test_timeout_with_task_mode(self):
        """Tool with timeout and task mode can be configured together."""
        mcp = FastMCP(tasks=True)

        @mcp.tool(task=True, timeout=1.0)
        async def task_with_timeout() -> str:
            await anyio.sleep(0.1)
            return "completed"

        # Tool should be registered successfully
        tools = await mcp.list_tools()
        tool = next((t for t in tools if t.name == "task_with_timeout"), None)
        assert tool is not None
        assert tool.timeout == 1.0
        assert tool.task_config.supports_tasks()

    async def test_multiple_tools_with_different_timeouts(self):
        """Multiple tools can have different timeout values."""
        mcp = FastMCP()

        @mcp.tool(timeout=1.0)
        async def short_timeout() -> str:
            await anyio.sleep(0.1)
            return "short"

        @mcp.tool(timeout=5.0)
        async def long_timeout() -> str:
            await anyio.sleep(0.1)
            return "long"

        @mcp.tool
        async def no_timeout() -> str:
            await anyio.sleep(0.1)
            return "none"

        # All should complete successfully
        result1 = await mcp.call_tool("short_timeout")
        result2 = await mcp.call_tool("long_timeout")
        result3 = await mcp.call_tool("no_timeout")

        assert isinstance(result1.content[0], TextContent)
        assert isinstance(result2.content[0], TextContent)
        assert isinstance(result3.content[0], TextContent)
        assert result1.content[0].text == "short"
        assert result2.content[0].text == "long"
        assert result3.content[0].text == "none"

    async def test_timeout_error_converted_to_tool_error(self):
        """Timeout errors are converted to ToolError by FastMCP."""
        mcp = FastMCP()

        @mcp.tool(timeout=0.1)
        async def times_out() -> str:
            await anyio.sleep(1.0)
            return "never"

        # TimeoutError should be caught and converted to ToolError
        with pytest.raises((ToolError, McpError)):
            await mcp.call_tool("times_out")
