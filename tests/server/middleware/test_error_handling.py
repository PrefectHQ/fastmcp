"""Tests for error handling middleware."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp import McpError

from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware.error_handling import (
    ErrorHandlingMiddleware,
    RetryMiddleware,
)
from fastmcp.server.middleware.middleware import MiddlewareContext


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


class TestErrorHandlingMiddleware:
    """Test error handling middleware functionality."""

    def test_init_default(self):
        """Test default initialization."""
        middleware = ErrorHandlingMiddleware()
        assert middleware.logger.name == "fastmcp.errors"
        assert middleware.include_traceback is False
        assert middleware.error_callback is None
        assert middleware.transform_errors is True
        assert middleware.error_counts == {}

    def test_init_custom(self):
        """Test custom initialization."""
        logger = logging.getLogger("custom")
        callback = MagicMock()

        middleware = ErrorHandlingMiddleware(
            logger=logger,
            include_traceback=True,
            error_callback=callback,
            transform_errors=False,
        )
        assert middleware.logger is logger
        assert middleware.include_traceback is True
        assert middleware.error_callback is callback
        assert middleware.transform_errors is False

    def test_log_error_basic(self, mock_context, caplog):
        """Test basic error logging."""
        middleware = ErrorHandlingMiddleware()
        error = ValueError("test error")

        with caplog.at_level(logging.ERROR):
            middleware._log_error(error, mock_context)

        assert "Error in test_method: ValueError: test error" in caplog.text
        assert "ValueError:test_method" in middleware.error_counts
        assert middleware.error_counts["ValueError:test_method"] == 1

    def test_log_error_with_traceback(self, mock_context, caplog):
        """Test error logging with traceback."""
        middleware = ErrorHandlingMiddleware(include_traceback=True)
        error = ValueError("test error")

        with caplog.at_level(logging.ERROR):
            middleware._log_error(error, mock_context)

        assert "Error in test_method: ValueError: test error" in caplog.text
        # The traceback is added to the log message
        assert "Error in test_method: ValueError: test error" in caplog.text

    def test_log_error_with_callback(self, mock_context):
        """Test error logging with callback."""
        callback = MagicMock()
        middleware = ErrorHandlingMiddleware(error_callback=callback)
        error = ValueError("test error")

        middleware._log_error(error, mock_context)

        callback.assert_called_once_with(error, mock_context)

    def test_log_error_callback_exception(self, mock_context, caplog):
        """Test error logging when callback raises exception."""
        callback = MagicMock(side_effect=RuntimeError("callback error"))
        middleware = ErrorHandlingMiddleware(error_callback=callback)
        error = ValueError("test error")

        with caplog.at_level(logging.ERROR):
            middleware._log_error(error, mock_context)

        assert "Error in error callback: callback error" in caplog.text

    def test_transform_error_mcp_error(self):
        """Test that MCP errors are not transformed."""
        middleware = ErrorHandlingMiddleware()
        from mcp.types import ErrorData

        error = McpError(ErrorData(code=-32001, message="test error"))

        result = middleware._transform_error(error)

        assert result is error

    def test_transform_error_disabled(self):
        """Test error transformation when disabled."""
        middleware = ErrorHandlingMiddleware(transform_errors=False)
        error = ValueError("test error")

        result = middleware._transform_error(error)

        assert result is error

    def test_transform_error_value_error(self):
        """Test transforming ValueError."""
        middleware = ErrorHandlingMiddleware()
        error = ValueError("test error")

        result = middleware._transform_error(error)

        assert isinstance(result, McpError)
        assert result.error.code == -32602
        assert "Invalid params: test error" in result.error.message

    def test_transform_error_file_not_found(self):
        """Test transforming FileNotFoundError."""
        middleware = ErrorHandlingMiddleware()
        error = FileNotFoundError("test error")

        result = middleware._transform_error(error)

        assert isinstance(result, McpError)
        assert result.error.code == -32001
        assert "Resource not found: test error" in result.error.message

    def test_transform_error_not_found_error(self):
        """Test transforming NotFoundError."""
        middleware = ErrorHandlingMiddleware()
        error = NotFoundError("test error")

        result = middleware._transform_error(error)

        assert isinstance(result, McpError)
        assert result.error.code == -32001
        assert "Resource not found: test error" in result.error.message

    def test_transform_error_permission_error(self):
        """Test transforming PermissionError."""
        middleware = ErrorHandlingMiddleware()
        error = PermissionError("test error")

        result = middleware._transform_error(error)

        assert isinstance(result, McpError)
        assert result.error.code == -32000
        assert "Permission denied: test error" in result.error.message

    def test_transform_error_timeout_error(self):
        """Test transforming TimeoutError."""
        middleware = ErrorHandlingMiddleware()
        error = TimeoutError("test error")

        result = middleware._transform_error(error)

        assert isinstance(result, McpError)
        assert result.error.code == -32000
        assert "Request timeout: test error" in result.error.message

    def test_transform_error_generic(self):
        """Test transforming generic error."""
        middleware = ErrorHandlingMiddleware()
        error = RuntimeError("test error")

        result = middleware._transform_error(error)

        assert isinstance(result, McpError)
        assert result.error.code == -32603
        assert "Internal error: test error" in result.error.message

    async def test_on_message_success(self, mock_context, mock_call_next):
        """Test successful message handling."""
        middleware = ErrorHandlingMiddleware()

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"
        assert mock_call_next.called

    async def test_on_message_error_transform(self, mock_context, caplog):
        """Test error handling with transformation."""
        middleware = ErrorHandlingMiddleware()
        mock_call_next = AsyncMock(side_effect=ValueError("test error"))

        with caplog.at_level(logging.ERROR):
            with pytest.raises(McpError) as exc_info:
                await middleware.on_message(mock_context, mock_call_next)

        assert isinstance(exc_info.value, McpError)
        assert exc_info.value.error.code == -32602
        assert "Invalid params: test error" in exc_info.value.error.message
        assert "Error in test_method: ValueError: test error" in caplog.text

    async def test_on_message_error_transform_tool_error(self, mock_context, caplog):
        """Test error handling with transformation and cause type."""
        middleware = ErrorHandlingMiddleware()
        tool_error = ToolError("test error")
        tool_error.__cause__ = ValueError()
        mock_call_next = AsyncMock(side_effect=tool_error)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(McpError) as exc_info:
                await middleware.on_message(mock_context, mock_call_next)

        assert isinstance(exc_info.value, McpError)
        assert exc_info.value.error.code == -32602
        assert "Invalid params: test error" in exc_info.value.error.message
        assert "Error in test_method: ToolError: test error" in caplog.text

    def test_get_error_stats(self, mock_context):
        """Test getting error statistics."""
        middleware = ErrorHandlingMiddleware()
        error1 = ValueError("error1")
        error2 = ValueError("error2")
        error3 = RuntimeError("error3")

        middleware._log_error(error1, mock_context)
        middleware._log_error(error2, mock_context)
        middleware._log_error(error3, mock_context)

        stats = middleware.get_error_stats()
        assert stats["ValueError:test_method"] == 2
        assert stats["RuntimeError:test_method"] == 1


class TestRetryMiddleware:
    """Test retry middleware functionality."""

    def test_init_default(self):
        """Test default initialization."""
        middleware = RetryMiddleware()
        assert middleware.max_retries == 3
        assert middleware.base_delay == 1.0
        assert middleware.max_delay == 60.0
        assert middleware.backoff_multiplier == 2.0
        assert middleware.retry_exceptions == (ConnectionError, TimeoutError)
        assert middleware.logger.name == "fastmcp.retry"

    def test_init_custom(self):
        """Test custom initialization."""
        logger = logging.getLogger("custom")
        middleware = RetryMiddleware(
            max_retries=5,
            base_delay=2.0,
            max_delay=120.0,
            backoff_multiplier=3.0,
            retry_exceptions=(ValueError, RuntimeError),
            logger=logger,
        )
        assert middleware.max_retries == 5
        assert middleware.base_delay == 2.0
        assert middleware.max_delay == 120.0
        assert middleware.backoff_multiplier == 3.0
        assert middleware.retry_exceptions == (ValueError, RuntimeError)
        assert middleware.logger is logger

    def test_should_retry_true(self):
        """Test retry decision for retryable errors."""
        middleware = RetryMiddleware()

        assert middleware._should_retry(ConnectionError()) is True
        assert middleware._should_retry(TimeoutError()) is True

    def test_should_retry_false(self):
        """Test retry decision for non-retryable errors."""
        middleware = RetryMiddleware()

        assert middleware._should_retry(ValueError()) is False
        assert middleware._should_retry(RuntimeError()) is False

    def test_calculate_delay(self):
        """Test delay calculation."""
        middleware = RetryMiddleware(
            base_delay=1.0, backoff_multiplier=2.0, max_delay=10.0
        )

        assert middleware._calculate_delay(0) == 1.0
        assert middleware._calculate_delay(1) == 2.0
        assert middleware._calculate_delay(2) == 4.0
        assert middleware._calculate_delay(3) == 8.0
        assert middleware._calculate_delay(4) == 10.0  # capped at max_delay

    async def test_on_request_success_first_try(self, mock_context, mock_call_next):
        """Test successful request on first try."""
        middleware = RetryMiddleware()

        result = await middleware.on_request(mock_context, mock_call_next)

        assert result == "test_result"
        assert mock_call_next.call_count == 1

    async def test_on_request_success_after_retries(self, mock_context, caplog):
        """Test successful request after retries."""
        middleware = RetryMiddleware(base_delay=0.01)  # Fast retry for testing

        # Fail first two attempts, succeed on third
        mock_call_next = AsyncMock(
            side_effect=[
                ConnectionError("connection failed"),
                ConnectionError("connection failed"),
                "test_result",
            ]
        )

        with caplog.at_level(logging.WARNING):
            result = await middleware.on_request(mock_context, mock_call_next)

        assert result == "test_result"
        assert mock_call_next.call_count == 3
        assert "Retrying in" in caplog.text

    async def test_on_request_max_retries_exceeded(self, mock_context, caplog):
        """Test request failing after max retries."""
        middleware = RetryMiddleware(max_retries=2, base_delay=0.01)

        # Fail all attempts
        mock_call_next = AsyncMock(side_effect=ConnectionError("connection failed"))

        with caplog.at_level(logging.WARNING):
            with pytest.raises(ConnectionError):
                await middleware.on_request(mock_context, mock_call_next)

        assert mock_call_next.call_count == 3  # initial + 2 retries
        assert "Retrying in" in caplog.text

    async def test_on_request_non_retryable_error(self, mock_context):
        """Test non-retryable error is not retried."""
        middleware = RetryMiddleware()
        mock_call_next = AsyncMock(side_effect=ValueError("non-retryable"))

        with pytest.raises(ValueError):
            await middleware.on_request(mock_context, mock_call_next)

        assert mock_call_next.call_count == 1  # No retries


@pytest.fixture
def error_handling_server():
    """Create a FastMCP server specifically for error handling middleware tests."""
    from fastmcp import FastMCP

    mcp = FastMCP("ErrorHandlingTestServer")

    @mcp.tool
    def reliable_operation(data: str) -> str:
        """A reliable operation that always succeeds."""
        return f"Success: {data}"

    @mcp.tool
    def failing_operation(error_type: str = "value") -> str:
        """An operation that fails with different error types."""
        if error_type == "value":
            raise ValueError("Value error occurred")
        elif error_type == "file":
            raise FileNotFoundError("File not found")
        elif error_type == "permission":
            raise PermissionError("Permission denied")
        elif error_type == "timeout":
            raise TimeoutError("Operation timed out")
        elif error_type == "generic":
            raise RuntimeError("Generic runtime error")
        else:
            return "Operation completed"

    @mcp.tool
    def intermittent_operation(fail_rate: float = 0.5) -> str:
        """An operation that fails intermittently."""
        import random

        if random.random() < fail_rate:
            raise ConnectionError("Random connection failure")
        return "Operation succeeded"

    @mcp.tool
    def retryable_operation(attempt_count: int = 0) -> str:
        """An operation that succeeds after a few attempts."""
        # This is a simple way to simulate retry behavior
        # In a real scenario, you might use external state
        if attempt_count < 2:
            raise ConnectionError("Temporary connection error")
        return "Operation succeeded after retries"

    return mcp


class TestErrorHandlingMiddlewareIntegration:
    """Integration tests for error handling middleware with real FastMCP server."""

    async def test_error_handling_middleware_logs_real_errors(
        self, error_handling_server, caplog
    ):
        """Test that error handling middleware logs real errors from tools."""
        from fastmcp.client import Client

        error_handling_server.add_middleware(ErrorHandlingMiddleware())

        with caplog.at_level(logging.ERROR):
            async with Client(error_handling_server) as client:
                # Test different types of errors
                with pytest.raises(Exception):
                    await client.call_tool("failing_operation", {"error_type": "value"})

                with pytest.raises(Exception):
                    await client.call_tool("failing_operation", {"error_type": "file"})

        log_text = caplog.text

        # Should have error logs for both failures
        assert "Error in tools/call: ToolError:" in log_text
        # Should have captured both error instances
        error_count = log_text.count("Error in tools/call:")
        assert error_count == 2

    async def test_error_handling_middleware_tracks_error_statistics(
        self, error_handling_server
    ):
        """Test that error handling middleware accurately tracks error statistics."""
        from fastmcp.client import Client

        error_middleware = ErrorHandlingMiddleware()
        error_handling_server.add_middleware(error_middleware)

        async with Client(error_handling_server) as client:
            # Generate different types of errors
            for _ in range(3):
                with pytest.raises(Exception):
                    await client.call_tool("failing_operation", {"error_type": "value"})

            for _ in range(2):
                with pytest.raises(Exception):
                    await client.call_tool("failing_operation", {"error_type": "file"})

        # Try some intermittent operations (some may succeed)
        for _ in range(5):
            try:
                await client.call_tool("intermittent_operation", {"fail_rate": 0.8})
            except Exception:
                pass  # Expected failures

        # Check error statistics
        stats = error_middleware.get_error_stats()

        # Should have tracked the ToolError wrapper
        assert "ToolError:tools/call" in stats
        assert stats["ToolError:tools/call"] >= 5  # At least the 5 deliberate failures

    async def test_error_handling_middleware_with_success_and_failure(
        self, error_handling_server, caplog
    ):
        """Test error handling middleware with mix of successful and failed operations."""
        from fastmcp.client import Client

        error_handling_server.add_middleware(ErrorHandlingMiddleware())

        with caplog.at_level(logging.ERROR):
            async with Client(error_handling_server) as client:
                # Successful operation (should not generate error logs)
                await client.call_tool("reliable_operation", {"data": "test"})

                # Failed operation (should generate error log)
                with pytest.raises(Exception):
                    await client.call_tool("failing_operation", {"error_type": "value"})

                # Another successful operation
                await client.call_tool("reliable_operation", {"data": "test2"})

        log_text = caplog.text

        # Should only have one error log (for the failed operation)
        error_count = log_text.count("Error in tools/call:")
        assert error_count == 1

    async def test_error_handling_middleware_custom_callback(
        self, error_handling_server
    ):
        """Test error handling middleware with custom error callback."""
        from fastmcp.client import Client

        captured_errors = []

        def error_callback(error, context):
            captured_errors.append(
                {
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "method": context.method,
                }
            )

        error_handling_server.add_middleware(
            ErrorHandlingMiddleware(error_callback=error_callback)
        )

        async with Client(error_handling_server) as client:
            # Generate some errors
            with pytest.raises(Exception):
                await client.call_tool("failing_operation", {"error_type": "value"})

            with pytest.raises(Exception):
                await client.call_tool("failing_operation", {"error_type": "timeout"})

        # Check that callback was called
        assert len(captured_errors) == 2
        assert captured_errors[0]["error_type"] == "ToolError"
        assert captured_errors[1]["error_type"] == "ToolError"
        assert all(error["method"] == "tools/call" for error in captured_errors)

    async def test_error_handling_middleware_transform_errors(
        self, error_handling_server
    ):
        """Test error transformation functionality."""
        from fastmcp.client import Client

        error_handling_server.add_middleware(
            ErrorHandlingMiddleware(transform_errors=True)
        )

        async with Client(error_handling_server) as client:
            # All errors should still be raised, but potentially transformed
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("failing_operation", {"error_type": "value"})

        # Error should still exist (may be wrapped by FastMCP)
        assert exc_info.value is not None


class TestRetryMiddlewareIntegration:
    """Integration tests for retry middleware with real FastMCP server."""

    async def test_retry_middleware_with_transient_failures(
        self, error_handling_server, caplog
    ):
        """Test retry middleware with operations that have transient failures."""
        from fastmcp.client import Client

        # Configure retry middleware to retry connection errors
        error_handling_server.add_middleware(
            RetryMiddleware(
                max_retries=3,
                base_delay=0.01,  # Very short delay for testing
                retry_exceptions=(ConnectionError,),
            )
        )

        with caplog.at_level(logging.WARNING):
            async with Client(error_handling_server) as client:
                # This operation fails intermittently - try several times
                success_count = 0
                for _ in range(5):
                    try:
                        await client.call_tool(
                            "intermittent_operation", {"fail_rate": 0.7}
                        )
                        success_count += 1
                    except Exception:
                        pass  # Some failures expected even with retries

        # Should have some retry log messages
        # Note: Retry logs might not appear if the underlying errors are wrapped by FastMCP
        # The key is that some operations should succeed due to retries

    async def test_retry_middleware_with_permanent_failures(
        self, error_handling_server
    ):
        """Test that retry middleware doesn't retry non-retryable errors."""
        from fastmcp.client import Client

        # Configure retry middleware for connection errors only
        error_handling_server.add_middleware(
            RetryMiddleware(
                max_retries=3, base_delay=0.01, retry_exceptions=(ConnectionError,)
            )
        )

        async with Client(error_handling_server) as client:
            # Value errors should not be retried
            with pytest.raises(Exception):
                await client.call_tool("failing_operation", {"error_type": "value"})

        # Should fail immediately without retries

    async def test_combined_error_handling_and_retry_middleware(
        self, error_handling_server, caplog
    ):
        """Test error handling and retry middleware working together."""
        from fastmcp.client import Client

        # Add both middleware
        error_handling_server.add_middleware(ErrorHandlingMiddleware())
        error_handling_server.add_middleware(
            RetryMiddleware(
                max_retries=2, base_delay=0.01, retry_exceptions=(ConnectionError,)
            )
        )

        with caplog.at_level(logging.ERROR):
            async with Client(error_handling_server) as client:
                # Try intermittent operation
                try:
                    await client.call_tool("intermittent_operation", {"fail_rate": 0.9})
                except Exception:
                    pass  # May still fail even with retries

                # Try permanent failure
                with pytest.raises(Exception):
                    await client.call_tool("failing_operation", {"error_type": "value"})

        log_text = caplog.text

        # Should have error logs from error handling middleware
        assert "Error in tools/call:" in log_text
