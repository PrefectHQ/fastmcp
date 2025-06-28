"""Error handling middleware for consistent error responses and tracking."""

import asyncio
import logging
import traceback
from collections.abc import Callable
from typing import Any

from mcp import McpError
from mcp.types import ErrorData

from .middleware import CallNext, Middleware, MiddlewareContext


class ErrorHandlingMiddleware(Middleware):
    """Middleware that provides consistent error handling and logging.

    Catches exceptions, logs them appropriately, and converts them to
    proper MCP error responses. Also tracks error patterns for monitoring.

    Example:
        ```python
        from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
        import logging

        # Configure logging to see error details
        logging.basicConfig(level=logging.ERROR)

        mcp = FastMCP("MyServer")
        mcp.add_middleware(ErrorHandlingMiddleware())
        ```
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        include_traceback: bool = False,
        error_callback: Callable[[Exception, MiddlewareContext], None] | None = None,
        transform_errors: bool = True,
    ):
        """Initialize error handling middleware.

        Args:
            logger: Logger instance for error logging. If None, uses 'fastmcp.errors'
            include_traceback: Whether to include full traceback in error logs
            error_callback: Optional callback function called for each error
            transform_errors: Whether to transform non-MCP errors to McpError
        """
        self.logger = logger or logging.getLogger("fastmcp.errors")
        self.include_traceback = include_traceback
        self.error_callback = error_callback
        self.transform_errors = transform_errors
        self.error_counts = {}

    def _log_error(self, error: Exception, context: MiddlewareContext) -> None:
        """Log error with appropriate detail level."""
        error_type = type(error).__name__
        method = context.method or "unknown"

        # Track error counts
        error_key = f"{error_type}:{method}"
        self.error_counts[error_key] = self.error_counts.get(error_key, 0) + 1

        base_message = f"Error in {method}: {error_type}: {str(error)}"

        if self.include_traceback:
            self.logger.error(f"{base_message}\n{traceback.format_exc()}")
        else:
            self.logger.error(base_message)

        # Call custom error callback if provided
        if self.error_callback:
            try:
                self.error_callback(error, context)
            except Exception as callback_error:
                self.logger.error(f"Error in error callback: {callback_error}")

    def _transform_error(self, error: Exception) -> Exception:
        """Transform non-MCP errors to proper MCP errors."""
        if isinstance(error, McpError):
            return error

        if not self.transform_errors:
            return error

        # Map common exceptions to appropriate MCP error codes
        error_type = type(error)

        if error_type in (ValueError, TypeError):
            return McpError(
                ErrorData(code=-32602, message=f"Invalid params: {str(error)}")
            )
        elif error_type in (FileNotFoundError, KeyError):
            return McpError(
                ErrorData(code=-32001, message=f"Resource not found: {str(error)}")
            )
        elif error_type is PermissionError:
            return McpError(
                ErrorData(code=-32000, message=f"Permission denied: {str(error)}")
            )
        elif error_type in (TimeoutError, asyncio.TimeoutError):
            return McpError(
                ErrorData(code=-32000, message=f"Request timeout: {str(error)}")
            )
        else:
            return McpError(
                ErrorData(code=-32603, message=f"Internal error: {str(error)}")
            )

    async def on_message(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Handle errors for all messages."""
        try:
            return await call_next(context)
        except Exception as error:
            self._log_error(error, context)

            # Transform and re-raise
            transformed_error = self._transform_error(error)
            raise transformed_error

    def get_error_stats(self) -> dict[str, int]:
        """Get error statistics for monitoring."""
        return self.error_counts.copy()


class RetryMiddleware(Middleware):
    """Middleware that implements automatic retry logic for failed requests.

    Retries requests that fail with transient errors, using exponential
    backoff to avoid overwhelming the server or external dependencies.

    Example:
        ```python
        from fastmcp.server.middleware.error_handling import RetryMiddleware

        # Retry up to 3 times with exponential backoff
        retry_middleware = RetryMiddleware(
            max_retries=3,
            retry_exceptions=(ConnectionError, TimeoutError)
        )

        mcp = FastMCP("MyServer")
        mcp.add_middleware(retry_middleware)
        ```
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_multiplier: float = 2.0,
        retry_exceptions: tuple[type[Exception], ...] = (ConnectionError, TimeoutError),
        logger: logging.Logger | None = None,
    ):
        """Initialize retry middleware.

        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Initial delay between retries in seconds
            max_delay: Maximum delay between retries in seconds
            backoff_multiplier: Multiplier for exponential backoff
            retry_exceptions: Tuple of exception types that should trigger retries
            logger: Logger for retry attempts
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_multiplier = backoff_multiplier
        self.retry_exceptions = retry_exceptions
        self.logger = logger or logging.getLogger("fastmcp.retry")

    def _should_retry(self, error: Exception) -> bool:
        """Determine if an error should trigger a retry."""
        return isinstance(error, self.retry_exceptions)

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for the given attempt number."""
        delay = self.base_delay * (self.backoff_multiplier**attempt)
        return min(delay, self.max_delay)

    async def on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Implement retry logic for requests."""
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                return await call_next(context)
            except Exception as error:
                last_error = error

                # Don't retry on the last attempt or if it's not a retryable error
                if attempt == self.max_retries or not self._should_retry(error):
                    break

                delay = self._calculate_delay(attempt)
                self.logger.warning(
                    f"Request {context.method} failed (attempt {attempt + 1}/{self.max_retries + 1}): "
                    f"{type(error).__name__}: {str(error)}. Retrying in {delay:.1f}s..."
                )

                await asyncio.sleep(delay)

        # Re-raise the last error if all retries failed
        if last_error:
            raise last_error
