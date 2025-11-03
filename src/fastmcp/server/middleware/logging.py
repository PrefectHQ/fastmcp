"""Comprehensive logging middleware for FastMCP servers."""

import json
import logging
import time
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

    def _format_message(self, message: dict[str, str | int | float]) -> str:
        """Format a message for logging."""
        if self.structured_logging:
            return json.dumps(message)
        else:
            return " ".join([f"{k}={v}" for k, v in message.items()])

    def _create_before_message(
        self, context: MiddlewareContext[Any]
    ) -> dict[str, str | int | float]:
        message = {
            "event": context.type + "_start",
            "method": context.method or "unknown",
            "source": context.source,
        }

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

    def _create_error_message(
        self,
        context: MiddlewareContext[Any],
        start_time: float,
        error: Exception,
    ) -> dict[str, str | int | float]:
        duration_ms: float = _get_duration_ms(start_time)
        message = {
            "event": context.type + "_error",
            "method": context.method or "unknown",
            "source": context.source,
            "duration_ms": duration_ms,
            "error": str(object=error),
        }
        return message

    def _create_after_message(
        self,
        context: MiddlewareContext[Any],
        start_time: float,
    ) -> dict[str, str | int | float]:
        duration_ms: float = _get_duration_ms(start_time)
        message = {
            "event": context.type + "_success",
            "method": context.method or "unknown",
            "source": context.source,
            "duration_ms": duration_ms,
        }
        return message

    def _log_message(
        self, message: dict[str, str | int | float], log_level: int | None = None
    ):
        self.logger.log(log_level or self.log_level, self._format_message(message))

    async def on_message(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> Any:
        """Log messages for configured methods."""

        if self.methods and context.method not in self.methods:
            return await call_next(context)

        self._log_message(self._create_before_message(context))

        start_time = time.perf_counter()
        try:
            result = await call_next(context)

            self._log_message(self._create_after_message(context, start_time))

            return result
        except Exception as e:
            self._log_message(
                self._create_error_message(context, start_time, e), logging.ERROR
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
        self.logger: Logger = logger or logging.getLogger("fastmcp.middleware.logging")
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
        self.logger: Logger = logger or logging.getLogger(
            "fastmcp.middleware.structured_logging"
        )
        self.log_level: int = log_level
        self.include_payloads: bool = include_payloads
        self.include_payload_length: bool = include_payload_length
        self.estimate_payload_tokens: bool = estimate_payload_tokens
        self.methods: list[str] | None = methods
        self.payload_serializer: Callable[[Any], str] | None = payload_serializer
        self.max_payload_length: int | None = None
        self.structured_logging: bool = True


def _get_duration_ms(start_time: float, /) -> float:
    return round(number=(time.perf_counter() - start_time) * 1000, ndigits=2)
