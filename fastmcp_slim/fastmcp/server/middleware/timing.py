"""Timing middleware for measuring and logging request performance."""

import logging
import time
from typing import Any

from .middleware import CallNext, Middleware, MiddlewareContext


class TimingMiddleware(Middleware):
    """Middleware that logs the execution time of requests.

    Only measures and logs timing for request messages (not notifications).
    Provides insights into performance characteristics of your MCP server.

    Example:
        ```python
        from fastmcp.server.middleware.timing import TimingMiddleware

        mcp = FastMCP("MyServer")
        mcp.add_middleware(TimingMiddleware())

        # Now all requests will be timed and logged
        ```
    """

    def __init__(
        self, logger: logging.Logger | None = None, log_level: int = logging.INFO
    ):
        """Initialize timing middleware.

        Args:
            logger: Logger instance to use. If None, creates a logger named 'fastmcp.timing'
            log_level: Log level for timing messages (default: INFO)
        """
        self.logger = logger or logging.getLogger("fastmcp.timing")
        self.log_level = log_level

    async def on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Time request execution and log the results."""
        method = context.method or "unknown"

        start_time = time.perf_counter()
        try:
            result = await call_next(context)
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.logger.log(
                self.log_level, f"Request {method} completed in {duration_ms:.2f}ms"
            )
            return result
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.logger.log(
                self.log_level,
                f"Request {method} failed after {duration_ms:.2f}ms: {e}",
            )
            raise


class DetailedTimingMiddleware(Middleware):
    """Enhanced timing middleware with per-operation breakdowns.

    Provides detailed timing information for different types of MCP operations,
    allowing you to identify performance bottlenecks in specific operations.

    Example:
        ```python
        from fastmcp.server.middleware.timing import DetailedTimingMiddleware
        import logging

        # Configure logging to see the output
        logging.basicConfig(level=logging.INFO)

        mcp = FastMCP("MyServer")
        mcp.add_middleware(DetailedTimingMiddleware())
        ```
    """

    def __init__(
        self, logger: logging.Logger | None = None, log_level: int = logging.INFO
    ):
        """Initialize detailed timing middleware.

        Args:
            logger: Logger instance to use. If None, creates a logger named 'fastmcp.timing.detailed'
            log_level: Log level for timing messages (default: INFO)
        """
        self.logger = logger or logging.getLogger("fastmcp.timing.detailed")
        self.log_level = log_level

    async def _time_operation(
        self, context: MiddlewareContext, call_next: CallNext, operation_name: str
    ) -> Any:
        """Helper method to time any operation."""
        start_time = time.perf_counter()
        try:
            result = await call_next(context)
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.logger.log(
                self.log_level, f"{operation_name} completed in {duration_ms:.2f}ms"
            )
            return result
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.logger.log(
                self.log_level,
                f"{operation_name} failed after {duration_ms:.2f}ms: {e}",
            )
            raise

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """Time tool execution."""
        tool_name = getattr(context.message, "name", "unknown")
        return await self._time_operation(context, call_next, f"Tool '{tool_name}'")

    async def on_read_resource(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """Time resource reading."""
        resource_uri = getattr(context.message, "uri", "unknown")
        return await self._time_operation(
            context, call_next, f"Resource '{resource_uri}'"
        )

    async def on_get_prompt(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """Time prompt retrieval."""
        prompt_name = getattr(context.message, "name", "unknown")
        return await self._time_operation(context, call_next, f"Prompt '{prompt_name}'")

    async def on_list_tools(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """Time tool listing."""
        return await self._time_operation(context, call_next, "List tools")

    async def on_list_resources(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """Time resource listing."""
        return await self._time_operation(context, call_next, "List resources")

    async def on_list_resource_templates(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """Time resource template listing."""
        return await self._time_operation(context, call_next, "List resource templates")

    async def on_list_prompts(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """Time prompt listing."""
        return await self._time_operation(context, call_next, "List prompts")
