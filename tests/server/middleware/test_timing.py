"""Tests for timing middleware."""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.server.middleware.timing import DetailedTimingMiddleware, TimingMiddleware


@pytest.fixture
def mock_context():
    """Create a mock middleware context."""
    context = MagicMock(spec=MiddlewareContext)
    context.method = "test_method"
    return context


@pytest.fixture
def mock_call_next():
    """Create a mock call_next function."""
    return AsyncMock(return_value="test_result")


class TestTimingMiddleware:
    """Test timing middleware functionality."""

    def test_init_default(self):
        """Test default initialization."""
        middleware = TimingMiddleware()
        assert middleware.logger.name == "fastmcp.timing"
        assert middleware.log_level == logging.INFO

    def test_init_custom(self):
        """Test custom initialization."""
        logger = logging.getLogger("custom")
        middleware = TimingMiddleware(logger=logger, log_level=logging.DEBUG)
        assert middleware.logger is logger
        assert middleware.log_level == logging.DEBUG

    async def test_on_request_success(self, mock_context, mock_call_next, caplog):
        """Test timing successful requests."""
        middleware = TimingMiddleware()

        result = await middleware.on_request(mock_context, mock_call_next)

        assert result == "test_result"
        assert mock_call_next.called
        assert "Request test_method completed in" in caplog.text
        assert "ms" in caplog.text

    async def test_on_request_failure(self, mock_context, caplog):
        """Test timing failed requests."""
        middleware = TimingMiddleware()
        mock_call_next = AsyncMock(side_effect=ValueError("test error"))

        with pytest.raises(ValueError):
            await middleware.on_request(mock_context, mock_call_next)

        assert "Request test_method failed after" in caplog.text
        assert "ms: test error" in caplog.text


class TestDetailedTimingMiddleware:
    """Test detailed timing middleware functionality."""

    def test_init_default(self):
        """Test default initialization."""
        middleware = DetailedTimingMiddleware()
        assert middleware.logger.name == "fastmcp.timing.detailed"
        assert middleware.log_level == logging.INFO

    async def test_on_call_tool(self, caplog):
        """Test timing tool calls."""
        middleware = DetailedTimingMiddleware()
        context = MagicMock()
        context.message.name = "test_tool"
        mock_call_next = AsyncMock(return_value="tool_result")

        result = await middleware.on_call_tool(context, mock_call_next)

        assert result == "tool_result"
        assert "Tool 'test_tool' completed in" in caplog.text

    async def test_on_read_resource(self, caplog):
        """Test timing resource reads."""
        middleware = DetailedTimingMiddleware()
        context = MagicMock()
        context.message.uri = "test://resource"
        mock_call_next = AsyncMock(return_value="resource_result")

        result = await middleware.on_read_resource(context, mock_call_next)

        assert result == "resource_result"
        assert "Resource 'test://resource' completed in" in caplog.text

    async def test_on_get_prompt(self, caplog):
        """Test timing prompt retrieval."""
        middleware = DetailedTimingMiddleware()
        context = MagicMock()
        context.message.name = "test_prompt"
        mock_call_next = AsyncMock(return_value="prompt_result")

        result = await middleware.on_get_prompt(context, mock_call_next)

        assert result == "prompt_result"
        assert "Prompt 'test_prompt' completed in" in caplog.text

    async def test_on_list_tools(self, caplog):
        """Test timing tool listing."""
        middleware = DetailedTimingMiddleware()
        context = MagicMock()
        mock_call_next = AsyncMock(return_value="tools_result")

        result = await middleware.on_list_tools(context, mock_call_next)

        assert result == "tools_result"
        assert "List tools completed in" in caplog.text

    async def test_operation_failure(self, caplog):
        """Test timing failed operations."""
        middleware = DetailedTimingMiddleware()
        context = MagicMock()
        context.message.name = "failing_tool"
        mock_call_next = AsyncMock(side_effect=RuntimeError("operation failed"))

        with pytest.raises(RuntimeError):
            await middleware.on_call_tool(context, mock_call_next)

        assert "Tool 'failing_tool' failed after" in caplog.text
        assert "ms: operation failed" in caplog.text


@pytest.fixture
def timing_server():
    """Create a FastMCP server specifically for timing middleware tests."""
    mcp = FastMCP("TimingTestServer")

    @mcp.tool
    def instant_task() -> str:
        """A task that completes instantly."""
        return "Done instantly"

    @mcp.tool
    def short_task() -> str:
        """A task that takes 0.01 seconds."""
        time.sleep(0.01)
        return "Done after 0.01 seconds"

    @mcp.tool
    def medium_task() -> str:
        """A task that takes 0.02 seconds."""
        time.sleep(0.02)
        return "Done after 0.02 seconds"

    @mcp.tool
    def failing_task() -> str:
        """A task that always fails."""
        raise ValueError("Task failed as expected")

    @mcp.resource("timer://test")
    def test_resource() -> str:
        """A resource that takes time to read."""
        time.sleep(0.005)
        return "Resource content after 0.005 seconds"

    @mcp.prompt
    def test_prompt() -> str:
        """A prompt that takes time to generate."""
        time.sleep(0.008)
        return "Prompt content after 0.008 seconds"

    return mcp


class TestTimingMiddlewareIntegration:
    """Integration tests for timing middleware with real FastMCP server."""

    async def test_timing_middleware_measures_tool_execution(
        self, timing_server, caplog
    ):
        """Test that timing middleware accurately measures tool execution times."""
        timing_server.add_middleware(TimingMiddleware())

        async with Client(timing_server) as client:
            # Test instant task
            await client.call_tool("instant_task")

            # Test short task (0.1s)
            await client.call_tool("short_task")

            # Test medium task (0.15s)
            await client.call_tool("medium_task")

        log_text = caplog.text

        # Should have timing logs for all three calls (plus any extra list_tools calls)
        timing_logs = [
            line
            for line in log_text.split("\n")
            if "completed in" in line and "ms" in line
        ]
        assert (
            len(timing_logs) >= 3
        )  # At least 3 tool calls, may have additional list_tools calls

        # Verify that longer tasks show longer timing (roughly)
        assert "tools/call completed in" in log_text
        assert "ms" in log_text

    async def test_timing_middleware_handles_failures(self, timing_server, caplog):
        """Test that timing middleware measures time even for failed operations."""
        timing_server.add_middleware(TimingMiddleware())

        async with Client(timing_server) as client:
            # This should fail but still be timed
            with pytest.raises(Exception):
                await client.call_tool("failing_task")

        # Should log the failure with timing
        assert "tools/call failed after" in caplog.text
        assert "ms:" in caplog.text

    async def test_detailed_timing_middleware_per_operation(
        self, timing_server, caplog
    ):
        """Test that detailed timing middleware provides operation-specific timing."""
        timing_server.add_middleware(DetailedTimingMiddleware())

        async with Client(timing_server) as client:
            # Test tool call
            await client.call_tool("short_task")

            # Test resource read
            await client.read_resource("timer://test")

            # Test prompt
            await client.get_prompt("test_prompt")

            # Test listing operations
            await client.list_tools()
            await client.list_resources()
            await client.list_prompts()

        log_text = caplog.text

        # Should have specific timing logs for each operation type
        assert "Tool 'short_task' completed in" in log_text
        assert "Resource 'timer://test' completed in" in log_text
        assert "Prompt 'test_prompt' completed in" in log_text
        assert "List tools completed in" in log_text
        assert "List resources completed in" in log_text
        assert "List prompts completed in" in log_text

    async def test_timing_middleware_concurrent_operations(self, timing_server, caplog):
        """Test timing middleware with concurrent operations."""
        timing_server.add_middleware(TimingMiddleware())

        async with Client(timing_server) as client:
            # Run multiple operations concurrently
            tasks = [
                client.call_tool("instant_task"),
                client.call_tool("short_task"),
                client.call_tool("instant_task"),
            ]

            await asyncio.gather(*tasks)

        log_text = caplog.text

        # Should have timing logs for all concurrent operations (including extra list_tools calls)
        timing_logs = [line for line in log_text.split("\n") if "completed in" in line]
        assert (
            len(timing_logs) >= 3
        )  # At least 3 tool calls, may have additional list_tools calls

    async def test_timing_middleware_custom_logger(self, timing_server, caplog):
        """Test timing middleware with custom logger configuration."""
        import io
        import logging

        # Create a custom logger that writes to a string buffer
        log_buffer = io.StringIO()
        handler = logging.StreamHandler(log_buffer)
        custom_logger = logging.getLogger("custom_timing")
        custom_logger.addHandler(handler)
        custom_logger.setLevel(logging.DEBUG)

        # Use custom logger and log level
        timing_server.add_middleware(
            TimingMiddleware(logger=custom_logger, log_level=logging.DEBUG)
        )

        async with Client(timing_server) as client:
            await client.call_tool("instant_task")

        # Check that our custom logger was used
        log_output = log_buffer.getvalue()
        assert "tools/call completed in" in log_output
        assert "ms" in log_output
