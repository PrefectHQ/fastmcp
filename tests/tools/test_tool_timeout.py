"""Tests for tool timeout functionality."""

import time
from contextlib import asynccontextmanager

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import TextContent

from fastmcp import Client, FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool


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
            await anyio.sleep(0.01)
            return "completed"

        result = await mcp.call_tool("fast_async_tool")
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "completed"

    async def test_timeout_not_reached_sync(self):
        """Sync tool with timeout completes before timeout."""
        mcp = FastMCP()

        @mcp.tool(timeout=5.0)
        def fast_sync_tool() -> str:
            time.sleep(0.01)
            return "completed"

        result = await mcp.call_tool("fast_sync_tool")
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "completed"

    async def test_async_timeout_exceeded(self):
        """Async tool exceeds timeout and raises TimeoutError."""
        mcp = FastMCP()

        @mcp.tool(timeout=0.2)
        async def slow_async_tool() -> str:
            await anyio.sleep(0.6)
            return "should not reach"

        # TimeoutError is caught and converted to ToolError by FastMCP
        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool("slow_async_tool")

        # Verify the tool raised an error (error message may be masked)
        assert exc_info.value is not None

    async def test_sync_timeout_exceeded(self):
        """A sync result returned after the deadline is a timeout error."""

        def slow_sync_tool() -> str:
            time.sleep(0.1)
            return "late result"

        tool = Tool.from_function(slow_sync_tool, timeout=0.01)

        with pytest.raises(MCPError) as exc_info:
            await tool.run({})

        assert exc_info.value.code == -32000
        assert "slow_sync_tool" in exc_info.value.message

    async def test_sync_timeout_preserves_dependency_lifetime(self):
        """Dependencies remain available until a timed-out worker finishes."""
        mcp = FastMCP()
        events: list[str] = []

        @asynccontextmanager
        async def resource():
            events.append("opened")
            try:
                yield events
            finally:
                events.append("closed")

        @mcp.tool(timeout=0.01)
        def slow_sync_tool(state: list[str] = Depends(resource)) -> str:
            state.append("started")
            time.sleep(0.1)
            state.append("finished")
            return "late result"

        with pytest.raises(ToolError, match="timed out"):
            await mcp.call_tool("slow_sync_tool")

        assert events == ["opened", "started", "finished", "closed"]

    async def test_timeout_error_raises_tool_error(self):
        """Timeout error is converted to ToolError and logs warning."""
        mcp = FastMCP()

        @mcp.tool(timeout=0.1)
        async def slow_tool() -> str:
            await anyio.sleep(0.3)
            return "never"

        # Verify that ToolError is raised (timeout warning is logged to stderr)
        with pytest.raises(ToolError):
            await mcp.call_tool("slow_tool")

    async def test_timeout_from_tool_from_function(self):
        """Timeout works when using Tool.from_function()."""
        from fastmcp.tools import Tool

        async def my_slow_tool() -> str:
            await anyio.sleep(0.3)
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
            await anyio.sleep(0.01)
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
            await anyio.sleep(0.01)
            return "short"

        @mcp.tool(timeout=5.0)
        async def long_timeout() -> str:
            await anyio.sleep(0.01)
            return "long"

        @mcp.tool
        async def no_timeout() -> str:
            await anyio.sleep(0.01)
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
            await anyio.sleep(0.3)
            return "never"

        # TimeoutError should be caught and converted to ToolError
        with pytest.raises((ToolError, MCPError)):
            await mcp.call_tool("times_out")

    @pytest.mark.parametrize("timeout", [None, 30.0])
    @pytest.mark.parametrize("is_async", [True, False])
    async def test_body_raised_timeout_error_is_not_reported_as_deadline(
        self, timeout: float | None, is_async: bool
    ):
        """A TimeoutError raised by the tool body itself (e.g. socket.timeout)
        must surface as the tool's own error, not as the deadline firing."""
        mcp = FastMCP()

        error = TimeoutError("upstream socket timed out")

        async def async_upstream_timeout() -> str:
            raise error

        def sync_upstream_timeout() -> str:
            raise error

        fn = async_upstream_timeout if is_async else sync_upstream_timeout
        tool = Tool.from_function(fn, name="upstream_timeout", timeout=timeout)
        mcp.add_tool(tool)

        with pytest.raises(TimeoutError) as original:
            await tool.run({})
        assert original.value is error

        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool("upstream_timeout")

        assert "timed out after" not in str(exc_info.value)
        assert "upstream socket timed out" in str(exc_info.value)

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    async def test_body_timeout_over_protocol(self, mode):
        mcp = FastMCP()

        @mcp.tool(timeout=30.0)
        async def upstream_timeout() -> str:
            raise TimeoutError("upstream socket timed out")

        async with Client(mcp, mode=mode) as client:
            result = await client.call_tool("upstream_timeout", raise_on_error=False)

        assert result.is_error
        assert isinstance(result.content[0], TextContent)
        assert "upstream socket timed out" in result.content[0].text
        assert "execution timed out after" not in result.content[0].text

    async def test_inner_deadline_preserves_timeout_error(self):
        async def inner_timeout() -> str:
            with anyio.fail_after(0):
                await anyio.sleep_forever()
            return "unreachable"

        tool = Tool.from_function(inner_timeout, timeout=30.0)
        with pytest.raises(TimeoutError):
            await tool.run({})

    async def test_outer_deadline_remains_mcp_error(self):
        async def slow_tool() -> str:
            await anyio.sleep_forever()
            return "unreachable"

        tool = Tool.from_function(slow_tool, timeout=0.01)
        with pytest.raises(MCPError) as exc_info:
            await tool.run({})
        assert exc_info.value.code == -32000
        assert (
            exc_info.value.message == "Tool 'slow_tool' execution timed out after 0.01s"
        )
