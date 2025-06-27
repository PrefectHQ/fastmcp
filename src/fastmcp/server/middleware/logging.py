"""Comprehensive logging middleware for FastMCP servers."""

import json
import logging
from typing import Any

from .middleware import CallNext, Middleware, MiddlewareContext


class LoggingMiddleware(Middleware):
    """Middleware that provides comprehensive request and response logging.

    Logs all MCP messages with configurable detail levels. Useful for debugging,
    monitoring, and understanding server usage patterns.

    Example:
        ```python
        from fastmcp.server.middleware.logging import LoggingMiddleware
        import logging

        # Configure logging
        logging.basicConfig(level=logging.INFO)

        mcp = FastMCP("MyServer")
        mcp.add_middleware(LoggingMiddleware())
        ```
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        log_level: int = logging.INFO,
        include_payloads: bool = False,
        max_payload_length: int = 1000,
        methods: list[str] | None = None,
    ):
        """Initialize logging middleware.

        Args:
            logger: Logger instance to use. If None, creates a logger named 'fastmcp.requests'
            log_level: Log level for messages (default: INFO)
            include_payloads: Whether to include message payloads in logs
            max_payload_length: Maximum length of payload to log (prevents huge logs)
            methods: List of methods to log. If None, logs all methods.
        """
        self.logger = logger or logging.getLogger("fastmcp.requests")
        self.log_level = log_level
        self.include_payloads = include_payloads
        self.max_payload_length = max_payload_length
        self.methods = methods

    def _format_message(self, context: MiddlewareContext) -> str:
        """Format a message for logging."""
        parts = [
            f"source={context.source}",
            f"type={context.type}",
            f"method={context.method or 'unknown'}",
        ]

        if self.include_payloads and hasattr(context.message, "__dict__"):
            try:
                payload = json.dumps(context.message.__dict__, default=str)
                if len(payload) > self.max_payload_length:
                    payload = payload[: self.max_payload_length] + "..."
                parts.append(f"payload={payload}")
            except (TypeError, ValueError):
                parts.append("payload=<non-serializable>")

        return " ".join(parts)

    async def on_message(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Log all messages."""
        message_info = self._format_message(context)
        if self.methods and context.method not in self.methods:
            return await call_next(context)

        self.logger.log(self.log_level, f"Processing message: {message_info}")

        try:
            result = await call_next(context)
            self.logger.log(
                self.log_level, f"Completed message: {context.method or 'unknown'}"
            )
            return result
        except Exception as e:
            self.logger.log(
                logging.ERROR, f"Failed message: {context.method or 'unknown'} - {e}"
            )
            raise


class StructuredLoggingMiddleware(Middleware):
    """Middleware that provides structured JSON logging for better log analysis.

    Outputs structured logs that are easier to parse and analyze with log
    aggregation tools like ELK stack, Splunk, or cloud logging services.

    Example:
        ```python
        from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
        import logging

        mcp = FastMCP("MyServer")
        mcp.add_middleware(StructuredLoggingMiddleware())
        ```
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        log_level: int = logging.INFO,
        include_payloads: bool = False,
        methods: list[str] | None = None,
    ):
        """Initialize structured logging middleware.

        Args:
            logger: Logger instance to use. If None, creates a logger named 'fastmcp.structured'
            log_level: Log level for messages (default: INFO)
            include_payloads: Whether to include message payloads in logs
            methods: List of methods to log. If None, logs all methods.
        """
        self.logger = logger or logging.getLogger("fastmcp.structured")
        self.log_level = log_level
        self.include_payloads = include_payloads
        self.methods = methods

    def _create_log_entry(
        self, context: MiddlewareContext, event: str, **extra_fields
    ) -> dict:
        """Create a structured log entry."""
        entry = {
            "event": event,
            "timestamp": context.timestamp.isoformat(),
            "source": context.source,
            "type": context.type,
            "method": context.method,
            **extra_fields,
        }

        if self.include_payloads and hasattr(context.message, "__dict__"):
            try:
                entry["payload"] = context.message.__dict__
            except (TypeError, ValueError):
                entry["payload"] = "<non-serializable>"

        return entry

    async def on_message(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Log structured message information."""
        start_entry = self._create_log_entry(context, "request_start")
        if self.methods and context.method not in self.methods:
            return await call_next(context)

        self.logger.log(self.log_level, json.dumps(start_entry))

        try:
            result = await call_next(context)

            success_entry = self._create_log_entry(
                context,
                "request_success",
                result_type=type(result).__name__ if result else None,
            )
            self.logger.log(self.log_level, json.dumps(success_entry))

            return result
        except Exception as e:
            error_entry = self._create_log_entry(
                context,
                "request_error",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            self.logger.log(logging.ERROR, json.dumps(error_entry))
            raise
