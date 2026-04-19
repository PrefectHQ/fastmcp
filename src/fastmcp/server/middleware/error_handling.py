"""Error handling middleware for consistent error responses and tracking."""

import logging
import traceback
from collections.abc import Callable
from typing import Any

import anyio
from mcp import McpError

from fastmcp.exceptions import FastMCPError

from .middleware import CallNext, Middleware, MiddlewareContext


class ErrorHandlingMiddleware(Middleware):
    """Middleware that provides consistent error handling and logging.

    Catches exceptions, logs them appropriately, and converts them to
    proper MCP error responses. Also tracks error patterns for monitoring.

    Example:
        ```python
        from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
        import logging

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
        self.logger = logger or logging.getLogger("fastmcp.errors")
        self.include_traceback = include_traceback
        self.error_callback = error_callback
        self.transform_errors = transform_errors
        self.error_counts: dict[str, int] = {}

    def _log_error(self, error: Exception, context: MiddlewareContext) -> None:
        error_type = type(error).__name__
        method = context.method or "unknown"
        error_key = f"{error_type}:{method}"
        self.error_counts[error_key] = self.error_counts.get(error_key, 0) + 1

        base_message = f"Error in {method}: {error_type}: {error!s}"

        if self.include_traceback:
            self.logger.error(f"{base_message}\n{traceback.format_exc()}")
        else:
            self.logger.error(base_message)

        if self.error_callback:
            try:
                self.error_callback(error, context)
            except Exception as callback_error:
                self.logger.error(f"Error in error callback: {callback_error}")

    def _transform_error(
        self, error: Exception, context: MiddlewareContext
    ) -> Exception:
        if isinstance(error, FastMCPError):
            return error.to_mcp_error()

        if isinstance(error, McpError):
            return error

        if not self.transform_errors:
            return error

        mcp_error = FastMCPError.from_generic_exception(
            error, method=context.method
        )
        return mcp_error.to_mcp_error()

    async def on_message(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        try:
            return await call_next(context)
        except Exception as error:
            self._log_error(error, context)
            transformed_error = self._transform_error(error, context)
            raise transformed_error from error

    def get_error_stats(self) -> dict[str, int]:
        return self.error_counts.copy()


class RetryMiddleware(Middleware):
    """Middleware that implements automatic retry logic for failed requests.

    Retries requests that fail with transient errors, using exponential
    backoff to avoid overwhelming the server or external dependencies.

    Example:
        ```python
        from fastmcp.server.middleware.error_handling import RetryMiddleware

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
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_multiplier = backoff_multiplier
        self.retry_exceptions = retry_exceptions
        self.logger = logger or logging.getLogger("fastmcp.retry")

    def _should_retry(self, error: Exception) -> bool:
        if isinstance(error, self.retry_exceptions):
            return True
        cause = error.__cause__
        return cause is not None and isinstance(cause, self.retry_exceptions)

    def _calculate_delay(self, attempt: int) -> float:
        delay = self.base_delay * (self.backoff_multiplier**attempt)
        return min(delay, self.max_delay)

    async def on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                return await call_next(context)
            except Exception as error:
                last_error = error

                if attempt == self.max_retries or not self._should_retry(error):
                    break

                delay = self._calculate_delay(attempt)
                self.logger.warning(
                    f"Request {context.method} failed (attempt {attempt + 1}/{self.max_retries + 1}): "
                    f"{type(error).__name__}: {error!s}. Retrying in {delay:.1f}s..."
                )

                await anyio.sleep(delay)

        if last_error:
            raise last_error
