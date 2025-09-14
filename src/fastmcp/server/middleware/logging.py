"""Comprehensive logging middleware for FastMCP servers."""

import json
import logging
from collections.abc import Callable
from logging import Logger
from typing import Any

import pydantic_core

from .middleware import CallNext, Middleware, MiddlewareContext


def default_serializer(data: Any) -> str:
    """The default serializer for Payloads in the logging middleware."""
    return pydantic_core.to_json(data, fallback=str).decode()


class BaseLoggingMiddleware(Middleware):
    """Base class for logging middleware."""

    logger: Logger
    log_level: int
    include_payloads: bool
    include_payload_length: bool
    estimate_payload_tokens: bool
    max_payload_length: int | None
    methods: list[str] | None
    structured_logging: bool
    payload_serializer: Callable[[Any], str] | None

    def _serialize_payload(self, context: MiddlewareContext[Any]) -> str:
        payload: str

        if not self.payload_serializer:
            payload = default_serializer(context.message)
        else:
            try:
                payload = self.payload_serializer(context.message)
            except Exception as e:
                self.logger.warning(
                    f"Failed to serialize payload due to {e}: {context.type} {context.method} {context.source}."
                )
                payload = default_serializer(context.message)

        return payload

    def _format_message(self, message: dict[str, str | int]) -> str:
        """Format a message for logging."""
        if self.structured_logging:
            return json.dumps(message)
        else:
            return " ".join([f"{k}={v}" for k, v in message.items()])

    def _get_timestamp_from_context(self, context: MiddlewareContext[Any]) -> str:
        """Get a timestamp from the context."""
        return context.timestamp.isoformat()

    def _create_before_message(
        self, context: MiddlewareContext[Any], event: str
    ) -> dict[str, str | int]:
        message = self._create_base_message(context, event)

        if (
            self.include_payloads
            or self.include_payload_length
            or self.estimate_payload_tokens
        ):
            payload = self._serialize_payload(context)

            if self.include_payload_length or self.estimate_payload_tokens:
                payload_length = len(payload)
                payload_tokens = payload_length // 4
                if self.estimate_payload_tokens:
                    message["payload_tokens"] = payload_tokens
                if self.include_payload_length:
                    message["payload_length"] = payload_length

            if self.max_payload_length and len(payload) > self.max_payload_length:
                payload = payload[: self.max_payload_length] + "..."

            if self.include_payloads:
                message["payload"] = payload
                message["payload_type"] = type(context.message).__name__

        return message

    def _create_after_message(
        self, context: MiddlewareContext[Any], event: str
    ) -> dict[str, str | int]:
        return self._create_base_message(context, event)

    def _create_base_message(
        self,
        context: MiddlewareContext[Any],
        event: str,
    ) -> dict[str, str | int]:
        """Format a message for logging."""

        parts: dict[str, str | int] = {
            "event": event,
            "timestamp": self._get_timestamp_from_context(context),
            "method": context.method or "unknown",
            "type": context.type,
            "source": context.source,
        }

        return parts

    async def on_message(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> Any:
        """Log all messages."""

        if self.methods and context.method not in self.methods:
            return await call_next(context)

        request_start_log_message = self._create_before_message(
            context, "request_start"
        )

        formatted_message = self._format_message(request_start_log_message)
        self.logger.log(self.log_level, f"Processing message: {formatted_message}")

        try:
            result = await call_next(context)

            request_success_log_message = self._create_after_message(
                context, "request_success"
            )

            formatted_message = self._format_message(request_success_log_message)
            self.logger.log(self.log_level, f"Completed message: {formatted_message}")

            return result
        except Exception as e:
            self.logger.log(
                logging.ERROR, f"Failed message: {context.method or 'unknown'} - {e}"
            )
            raise


class LoggingMiddleware(BaseLoggingMiddleware):
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
        *,
        logger: logging.Logger | None = None,
        log_level: int = logging.INFO,
        include_payloads: bool = False,
        include_payload_length: bool = False,
        estimate_payload_tokens: bool = False,
        max_payload_length: int = 1000,
        methods: list[str] | None = None,
        payload_serializer: Callable[[Any], str] | None = None,
    ):
        """Initialize logging middleware.

        Args:
            logger: Logger instance to use. If None, creates a logger named 'fastmcp.requests'
            log_level: Log level for messages (default: INFO)
            include_payloads: Whether to include message payloads in logs
            include_payload_length: Whether to include response size in logs
            estimate_payload_tokens: Whether to estimate response tokens
            max_payload_length: Maximum length of payload to log (prevents huge logs)
            methods: List of methods to log. If None, logs all methods.
            payload_serializer: Callable that converts objects to a JSON string for the
                payload. If not provided, uses FastMCP's default tool serializer.
        """
        self.logger: Logger = logger or logging.getLogger("fastmcp.requests")
        self.log_level = log_level
        self.include_payloads: bool = include_payloads
        self.include_payload_length: bool = include_payload_length
        self.estimate_payload_tokens: bool = estimate_payload_tokens
        self.max_payload_length: int = max_payload_length
        self.methods: list[str] | None = methods
        self.payload_serializer: Callable[[Any], str] | None = payload_serializer
        self.structured_logging: bool = False


class StructuredLoggingMiddleware(BaseLoggingMiddleware):
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
        *,
        logger: logging.Logger | None = None,
        log_level: int = logging.INFO,
        include_payloads: bool = False,
        include_payload_length: bool = False,
        estimate_payload_tokens: bool = False,
        methods: list[str] | None = None,
        payload_serializer: Callable[[Any], str] | None = None,
    ):
        """Initialize structured logging middleware.

        Args:
            logger: Logger instance to use. If None, creates a logger named 'fastmcp.structured'
            log_level: Log level for messages (default: INFO)
            include_payloads: Whether to include message payloads in logs
            include_payload_length: Whether to include payload size in logs
            estimate_payload_tokens: Whether to estimate token count using length // 4
            methods: List of methods to log. If None, logs all methods.
            payload_serializer: Callable that converts objects to a JSON string for the
                payload. If not provided, uses FastMCP's default tool serializer.
        """
        self.logger: Logger = logger or logging.getLogger("fastmcp.structured")
        self.log_level: int = log_level
        self.include_payloads: bool = include_payloads
        self.include_payload_length: bool = include_payload_length
        self.estimate_payload_tokens: bool = estimate_payload_tokens
        self.methods: list[str] | None = methods
        self.payload_serializer: Callable[[Any], str] | None = payload_serializer
        self.max_payload_length: int | None = None
        self.structured_logging: bool = True
