"""Tests for logging middleware."""

import datetime
import json
import logging
import re
from typing import Any, Literal, TypeVar
from unittest.mock import AsyncMock, MagicMock

import mcp
import mcp.types
import pytest
from inline_snapshot import snapshot
from pydantic import AnyUrl

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.middleware.logging import (
    LoggingMiddleware,
    StructuredLoggingMiddleware,
)
from fastmcp.server.middleware.middleware import CallNext, MiddlewareContext
from fastmcp.utilities.tests import caplog_for_fastmcp

FIXED_DATE = datetime.datetime(2023, 1, 1, tzinfo=datetime.timezone.utc)

T = TypeVar("T")


def remove_line_numbers(logs: str) -> str:
    """Remove line numbers from log messages."""
    trimmed_logs = ""
    lines = logs.split("\n")
    for line in lines:
        # Match only the first `:\d+ `
        line = re.sub(pattern=r":\d+ ", repl=":LINE_NUMBER ", string=line, count=1)
        trimmed_logs += line + "\n"
    return trimmed_logs


def new_mock_context(
    message: T,
    method: str | None = None,
    source: Literal["server", "client"] | None = None,
    type: Literal["request", "notification"] | None = None,
) -> MiddlewareContext[T]:
    """Create a new mock middleware context."""
    context = MagicMock(spec=MiddlewareContext[T])
    context.method = method or "test_method"
    context.source = source or "client"
    context.type = type or "request"
    context.message = message
    context.timestamp = FIXED_DATE
    return context


@pytest.fixture
def mock_context():
    """Create a mock middleware context."""

    return new_mock_context(
        message=mcp.types.CallToolRequest(
            method="tools/call",
            params=mcp.types.CallToolRequestParams(
                name="test_method",
                arguments={"param": "value"},
            ),
        )
    )


@pytest.fixture
def mock_call_next() -> AsyncMock:
    """Create a mock call_next function."""
    return AsyncMock(return_value="test_result")


class TestStructuredLoggingMiddleware:
    """Test structured logging middleware functionality."""

    def test_init_default(self):
        """Test default initialization."""
        middleware = LoggingMiddleware()

        assert middleware.logger.name == "fastmcp.requests"
        assert middleware.log_level == logging.INFO
        assert middleware.include_payloads is False
        assert middleware.max_payload_length == 1000
        assert middleware.include_payload_length is False
        assert middleware.estimate_payload_tokens is False
        assert middleware.structured_logging is False

    def test_init_custom(self):
        """Test custom initialization."""
        logger = logging.getLogger("custom")
        middleware = StructuredLoggingMiddleware(
            logger=logger,
            log_level=logging.DEBUG,
            include_payloads=True,
            include_payload_length=False,
            estimate_payload_tokens=True,
        )
        assert middleware.logger is logger
        assert middleware.log_level == logging.DEBUG
        assert middleware.include_payloads is True
        assert middleware.include_payload_length is False
        assert middleware.estimate_payload_tokens is True

    class TestHelperMethods:
        def test_create_before_message(self, mock_context: MiddlewareContext[Any]):
            """Test message formatting without payloads."""
            middleware = StructuredLoggingMiddleware()

            message = middleware._create_before_message(mock_context, "test_event")

            assert message == snapshot(
                {
                    "event": "test_event",
                    "timestamp": "2023-01-01T00:00:00+00:00",
                    "source": "client",
                    "type": "request",
                    "method": "test_method",
                }
            )

        def test_create_message_with_payloads(
            self, mock_context: MiddlewareContext[Any]
        ):
            """Test message formatting with payloads."""
            middleware = StructuredLoggingMiddleware(include_payloads=True)

            message = middleware._create_before_message(mock_context, "test_event")

            assert message == snapshot(
                {
                    "event": "test_event",
                    "timestamp": "2023-01-01T00:00:00+00:00",
                    "source": "client",
                    "type": "request",
                    "method": "test_method",
                    "payload": '{"method":"tools/call","params":{"_meta":null,"name":"test_method","arguments":{"param":"value"}}}',
                    "payload_type": "CallToolRequest",
                }
            )

        def test_calculate_response_size(self, mock_context: MiddlewareContext[Any]):
            """Test response size calculation."""
            middleware = StructuredLoggingMiddleware(include_payload_length=True)
            message = middleware._create_before_message(mock_context, "test_event")

            assert message == snapshot(
                {
                    "event": "test_event",
                    "timestamp": "2023-01-01T00:00:00+00:00",
                    "source": "client",
                    "type": "request",
                    "method": "test_method",
                    "payload_length": 98,
                }
            )

        def test_calculate_response_size_with_token_estimation(
            self, mock_context: MiddlewareContext[Any]
        ):
            """Test response size calculation with token estimation."""
            middleware = StructuredLoggingMiddleware(
                include_payload_length=True, estimate_payload_tokens=True
            )
            message = middleware._create_before_message(mock_context, "test_event")

            assert message == snapshot(
                {
                    "event": "test_event",
                    "timestamp": "2023-01-01T00:00:00+00:00",
                    "source": "client",
                    "type": "request",
                    "method": "test_method",
                    "payload_tokens": 24,
                    "payload_length": 98,
                }
            )

    async def test_on_message_success(
        self,
        mock_context: MiddlewareContext[Any],
        caplog: pytest.LogCaptureFixture,
    ):
        """Test logging successful messages."""
        middleware = StructuredLoggingMiddleware()
        mock_call_next = AsyncMock(return_value="test_result")

        with caplog_for_fastmcp(caplog):
            result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"
        assert mock_call_next.called
        assert remove_line_numbers(caplog.text) == snapshot("""\
INFO     fastmcp.structured:logging.py:LINE_NUMBER Processing message: {"event": "request_start", "timestamp": "2023-01-01T00:00:00+00:00", "method": "test_method", "type": "request", "source": "client"}
INFO     fastmcp.structured:logging.py:LINE_NUMBER Completed message: {"event": "request_success", "timestamp": "2023-01-01T00:00:00+00:00", "method": "test_method", "type": "request", "source": "client"}

""")

    async def test_on_message_failure(
        self, mock_context: MiddlewareContext[Any], caplog: pytest.LogCaptureFixture
    ):
        """Test logging failed messages."""
        middleware = StructuredLoggingMiddleware()
        mock_call_next = AsyncMock(side_effect=ValueError("test error"))

        with caplog_for_fastmcp(caplog):
            with pytest.raises(ValueError):
                await middleware.on_message(mock_context, mock_call_next)

        assert "Processing message:" in caplog.text
        assert "Failed message: test_method - test error" in caplog.text


class TestLoggingMiddleware:
    """Test structured logging middleware functionality."""

    def test_init_default(self):
        """Test default initialization."""
        middleware = LoggingMiddleware()
        assert middleware.logger.name == "fastmcp.requests"
        assert middleware.log_level == logging.INFO
        assert middleware.include_payloads is False
        assert middleware.include_payload_length is False
        assert middleware.estimate_payload_tokens is False

    def test_format_message(self, mock_context: MiddlewareContext[Any]):
        """Test message formatting."""
        middleware = LoggingMiddleware()
        message = middleware._create_before_message(mock_context, "test_event")
        formatted = middleware._format_message(message)

        assert formatted == snapshot(
            "event=test_event timestamp=2023-01-01T00:00:00+00:00 method=test_method type=request source=client"
        )

    def test_create_before_message_long_payload(
        self, mock_context: MiddlewareContext[Any]
    ):
        """Test message formatting with long payload truncation."""
        middleware = LoggingMiddleware(include_payloads=True, max_payload_length=10)

        message = middleware._create_before_message(mock_context, "test_event")

        formatted = middleware._format_message(message)

        assert "payload=" in formatted
        assert "..." in formatted

    async def test_on_message_failure(
        self, mock_context: MiddlewareContext[Any], caplog: pytest.LogCaptureFixture
    ):
        """Test structured logging of failed messages."""
        middleware = StructuredLoggingMiddleware()
        mock_call_next = AsyncMock(side_effect=ValueError("test error"))

        with caplog_for_fastmcp(caplog):
            with pytest.raises(ValueError):
                await middleware.on_message(mock_context, mock_call_next)

        # Check that we have structured JSON logs
        log_lines = [record.message for record in caplog.records]
        assert len(log_lines) == 2  # start and error entries

        # Extract JSON from "Processing message: {JSON}"
        start_message = log_lines[0]
        assert start_message.startswith("Processing message: ")
        start_json = start_message[len("Processing message: ") :]
        start_entry = json.loads(start_json)
        assert start_entry["event"] == "request_start"

        # Error messages have different format - check the second log entry
        assert "Failed message:" in log_lines[1]

    async def test_on_message_with_pydantic_types_in_payload(
        self,
        mock_call_next: CallNext[Any, Any],
        caplog: pytest.LogCaptureFixture,
    ):
        """Ensure Pydantic AnyUrl in payload serializes correctly when include_payloads=True."""

        mock_context = new_mock_context(
            message=mcp.types.ReadResourceRequest(
                method="resources/read",
                params=mcp.types.ReadResourceRequestParams(
                    uri=AnyUrl("test://example/1"),
                ),
            )
        )

        middleware = StructuredLoggingMiddleware(include_payloads=True)

        with caplog_for_fastmcp(caplog):
            result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"

        log_lines = [record.message for record in caplog.records]

        assert len(log_lines) == 2

        # Extract JSON from log messages
        start_message = log_lines[0]
        assert start_message.startswith("Processing message: ")
        start_json = start_message[len("Processing message: ") :]
        assert json.loads(start_json) == snapshot(
            {
                "event": "request_start",
                "timestamp": "2023-01-01T00:00:00+00:00",
                "source": "client",
                "type": "request",
                "method": "test_method",
                "payload": '{"method":"resources/read","params":{"_meta":null,"uri":"test://example/1"}}',
                "payload_type": "ReadResourceRequest",
            }
        )

        success_message = log_lines[1]
        assert success_message.startswith("Completed message: ")
        success_json = success_message[len("Completed message: ") :]
        assert json.loads(success_json) == snapshot(
            {
                "event": "request_success",
                "timestamp": "2023-01-01T00:00:00+00:00",
                "source": "client",
                "type": "request",
                "method": "test_method",
            }
        )

    async def test_on_message_with_resource_template_in_payload(
        self,
        mock_call_next: CallNext[Any, Any],
        caplog: pytest.LogCaptureFixture,
    ):
        """Ensure ResourceTemplate in payload serializes via pydantic conversion without errors."""

        mock_context = new_mock_context(
            message=ResourceTemplate(
                name="tmpl",
                uri_template="tmpl://{id}",
                parameters={"id": {"type": "string"}},
            )
        )

        middleware = StructuredLoggingMiddleware(include_payloads=True)

        with caplog_for_fastmcp(caplog):
            result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"

        log_lines = [record.message for record in caplog.records]
        assert len(log_lines) == 2

        # Extract JSON from log message
        start_message = log_lines[0]
        assert start_message.startswith("Processing message: ")
        start_json = start_message[len("Processing message: ") :]
        assert json.loads(start_json) == snapshot(
            {
                "event": "request_start",
                "timestamp": "2023-01-01T00:00:00+00:00",
                "source": "client",
                "type": "request",
                "method": "test_method",
                "payload": '{"name":"tmpl","title":null,"description":null,"tags":[],"meta":null,"enabled":true,"uri_template":"tmpl://{id}","mime_type":"text/plain","parameters":{"id":{"type":"string"}},"annotations":null}',
                "payload_type": "ResourceTemplate",
            }
        )

    async def test_on_message_with_nonserializable_payload_falls_back_to_str(
        self, mock_call_next: CallNext[Any, Any], caplog: pytest.LogCaptureFixture
    ):
        """Ensure non-JSONable objects fall back to string serialization in payload."""

        class NonSerializable:
            def __str__(self) -> str:
                return "NON_SERIALIZABLE"

        mock_context = new_mock_context(
            message=mcp.types.CallToolRequest(
                method="tools/call",
                params=mcp.types.CallToolRequestParams(
                    name="test_method",
                    arguments={"obj": NonSerializable()},
                ),
            )
        )

        middleware = StructuredLoggingMiddleware(include_payloads=True)

        with caplog_for_fastmcp(caplog):
            result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"

        log_lines = [record.message for record in caplog.records]
        assert len(log_lines) >= 2

        # Extract JSON from log message
        start_message = log_lines[0]
        assert start_message.startswith("Processing message: ")
        start_json = start_message[len("Processing message: ") :]
        assert json.loads(start_json) == snapshot(
            {
                "event": "request_start",
                "timestamp": "2023-01-01T00:00:00+00:00",
                "source": "client",
                "type": "request",
                "method": "test_method",
                "payload": '{"method":"tools/call","params":{"_meta":null,"name":"test_method","arguments":{"obj":"NON_SERIALIZABLE"}}}',
                "payload_type": "CallToolRequest",
            }
        )

    async def test_on_message_with_custom_serializer_applied(
        self, mock_call_next: CallNext[Any, Any], caplog: pytest.LogCaptureFixture
    ):
        """Ensure a custom serializer is used for non-JSONable payloads."""

        # Provide a serializer that replaces entire payload with a fixed string
        def custom_serializer(_: Any) -> str:
            return "CUSTOM_PAYLOAD"

        mock_context = new_mock_context(
            message=mcp.types.CallToolRequest(
                method="tools/call",
                params=mcp.types.CallToolRequestParams(
                    name="test_method",
                    arguments={"obj": "OBJECT"},
                ),
            )
        )

        middleware = StructuredLoggingMiddleware(
            include_payloads=True, payload_serializer=custom_serializer
        )

        with caplog_for_fastmcp(caplog):
            result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"

        log_lines = [record.message for record in caplog.records]
        assert len(log_lines) >= 2

        # Extract JSON from log message
        start_message = log_lines[0]
        assert start_message.startswith("Processing message: ")
        start_json = start_message[len("Processing message: ") :]
        assert json.loads(start_json) == snapshot(
            {
                "event": "request_start",
                "timestamp": "2023-01-01T00:00:00+00:00",
                "source": "client",
                "type": "request",
                "method": "test_method",
                "payload": "CUSTOM_PAYLOAD",
                "payload_type": "CallToolRequest",
            }
        )


@pytest.fixture
def logging_server():
    """Create a FastMCP server specifically for logging middleware tests."""
    from fastmcp import FastMCP

    mcp = FastMCP("LoggingTestServer")

    @mcp.tool
    def simple_operation(data: str) -> str:
        """A simple operation for testing logging."""
        return f"Processed: {data}"

    @mcp.tool
    def complex_operation(items: list[str], mode: str = "default") -> dict:
        """A complex operation with structured data."""
        return {"processed_items": len(items), "mode": mode, "result": "success"}

    @mcp.tool
    def operation_with_error(should_fail: bool = False) -> str:
        """An operation that can be made to fail."""
        if should_fail:
            raise ValueError("Operation failed intentionally")
        return "Operation completed successfully"

    @mcp.resource("log://test")
    def test_resource() -> str:
        """A test resource for logging."""
        return "Test resource content"

    @mcp.prompt
    def test_prompt() -> str:
        """A test prompt for logging."""
        return "Test prompt content"

    return mcp


class TestLoggingMiddlewareIntegration:
    """Integration tests for logging middleware with real FastMCP server."""

    @pytest.fixture
    def logging_server(self):
        """Create a FastMCP server specifically for logging middleware tests."""
        mcp = FastMCP("LoggingTestServer")

        @mcp.tool
        def simple_operation(data: str) -> str:
            """A simple operation for testing logging."""
            return f"Processed: {data}"

        @mcp.tool
        def complex_operation(items: list[str], mode: str = "default") -> dict:
            """A complex operation with structured data."""
            return {"processed_items": len(items), "mode": mode, "result": "success"}

        @mcp.tool
        def operation_with_error(should_fail: bool = False) -> str:
            """An operation that can be made to fail."""
            if should_fail:
                raise ValueError("Operation failed intentionally")
            return "Operation completed successfully"

        @mcp.resource("log://test")
        def test_resource() -> str:
            """A test resource for logging."""
            return "Test resource content"

        @mcp.prompt
        def test_prompt() -> str:
            """A test prompt for logging."""
            return "Test prompt content"

        return mcp

    async def test_logging_middleware_logs_successful_operations(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test that logging middleware captures successful operations."""
        logging_middleware = LoggingMiddleware(methods=["tools/call"])
        logging_middleware._get_timestamp_from_context = (  # ty: ignore[invalid-assignment]
            lambda _: FIXED_DATE.isoformat()
        )

        logging_server.add_middleware(logging_middleware)

        with caplog_for_fastmcp(caplog):
            with caplog.at_level(logging.INFO):
                async with Client(logging_server) as client:
                    await client.call_tool(
                        name="simple_operation", arguments={"data": "test_data"}
                    )
                    await client.call_tool(
                        name="complex_operation",
                        arguments={"items": ["a", "b", "c"], "mode": "batch"},
                    )

        # Should have processing and completion logs for both operations
        assert remove_line_numbers(caplog.text) == snapshot("""\
INFO     mcp.server.lowlevel.server:server.py:LINE_NUMBER Processing request of type CallToolRequest
INFO     fastmcp.requests:logging.py:LINE_NUMBER Processing message: event=request_start timestamp=2023-01-01T00:00:00+00:00 method=tools/call type=request source=client
INFO     fastmcp.requests:logging.py:LINE_NUMBER Completed message: event=request_success timestamp=2023-01-01T00:00:00+00:00 method=tools/call type=request source=client
INFO     mcp.server.lowlevel.server:server.py:LINE_NUMBER Processing request of type ListToolsRequest
INFO     mcp.server.lowlevel.server:server.py:LINE_NUMBER Processing request of type CallToolRequest
INFO     fastmcp.requests:logging.py:LINE_NUMBER Processing message: event=request_start timestamp=2023-01-01T00:00:00+00:00 method=tools/call type=request source=client
INFO     fastmcp.requests:logging.py:LINE_NUMBER Completed message: event=request_success timestamp=2023-01-01T00:00:00+00:00 method=tools/call type=request source=client

""")

    async def test_logging_middleware_logs_failures(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test that logging middleware captures failed operations."""
        logging_server.add_middleware(LoggingMiddleware(methods=["tools/call"]))

        with caplog_for_fastmcp(caplog):
            async with Client(logging_server) as client:
                # This should fail and be logged
                with pytest.raises(Exception):
                    await client.call_tool(
                        "operation_with_error", {"should_fail": True}
                    )

        log_text = caplog.text

        # Should have processing and failure logs
        assert "Processing message:" in log_text
        assert "Failed message: tools/call" in log_text

    async def test_logging_middleware_with_payloads(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test logging middleware when configured to include payloads."""

        middleware = LoggingMiddleware(
            include_payloads=True, max_payload_length=500, methods=["tools/call"]
        )
        middleware._get_timestamp_from_context = (  # ty: ignore[invalid-assignment]
            lambda _: FIXED_DATE.isoformat()
        )
        logging_server.add_middleware(middleware)

        with caplog_for_fastmcp(caplog):
            async with Client(logging_server) as client:
                await client.call_tool("simple_operation", {"data": "payload_test"})

        log_text = caplog.text

        # Remove client IDs from log text for consistent snapshots
        import re

        log_text = re.sub(r"\[Client-[^\]]+\]", "[Client-XXXX]", log_text)

        assert remove_line_numbers(log_text) == snapshot("""\
DEBUG    fastmcp.fastmcp.client.transports:transports.py:LINE_NUMBER Inferred transport: <FastMCPTransport(server='LoggingTestServer')>
DEBUG    fastmcp.fastmcp.client.client:client.py:LINE_NUMBER [Client-XXXX] called call_tool: simple_operation
DEBUG    fastmcp.fastmcp.server.server:server.py:LINE_NUMBER [LoggingTestServer] Handler called: list_tools
DEBUG    fastmcp.fastmcp.server.server:server.py:LINE_NUMBER [LoggingTestServer] Handler called: call_tool simple_operation with {'data': 'payload_test'}
INFO     fastmcp.requests:logging.py:LINE_NUMBER Processing message: event=request_start timestamp=2023-01-01T00:00:00+00:00 method=tools/call type=request source=client payload={"_meta":null,"name":"simple_operation","arguments":{"data":"payload_test"}} payload_type=CallToolRequestParams
INFO     fastmcp.requests:logging.py:LINE_NUMBER Completed message: event=request_success timestamp=2023-01-01T00:00:00+00:00 method=tools/call type=request source=client
DEBUG    fastmcp.fastmcp.server.server:server.py:LINE_NUMBER [LoggingTestServer] Handler called: list_tools

""")

    async def test_structured_logging_middleware_produces_json(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test that structured logging middleware produces parseable JSON logs."""

        logging_middleware = StructuredLoggingMiddleware(
            include_payloads=True, methods=["tools/call"]
        )
        logging_middleware._get_timestamp_from_context = (  # ty: ignore[invalid-assignment]
            lambda _: FIXED_DATE.isoformat()
        )

        logging_server.add_middleware(logging_middleware)

        with caplog_for_fastmcp(caplog):
            async with Client(logging_server) as client:
                await client.call_tool(
                    name="simple_operation", arguments={"data": "json_test"}
                )

        # Extract JSON log entries
        log_lines = [
            record.message
            for record in caplog.records
            if record.name == "fastmcp.structured"
        ]

        assert len(log_lines) >= 2  # Should have start and success entries

        # Remove client IDs from log text for consistent snapshots
        import re

        log_text = re.sub(r"\[Client-[^\]]+\]", "[Client-XXXX]", caplog.text)

        assert remove_line_numbers(log_text) == snapshot("""\
DEBUG    fastmcp.fastmcp.client.transports:transports.py:LINE_NUMBER Inferred transport: <FastMCPTransport(server='LoggingTestServer')>
DEBUG    fastmcp.fastmcp.client.client:client.py:LINE_NUMBER [Client-XXXX] called call_tool: simple_operation
DEBUG    fastmcp.fastmcp.server.server:server.py:LINE_NUMBER [LoggingTestServer] Handler called: list_tools
DEBUG    fastmcp.fastmcp.server.server:server.py:LINE_NUMBER [LoggingTestServer] Handler called: call_tool simple_operation with {'data': 'json_test'}
INFO     fastmcp.structured:logging.py:LINE_NUMBER Processing message: {"event": "request_start", "timestamp": "2023-01-01T00:00:00+00:00", "method": "tools/call", "type": "request", "source": "client", "payload": "{\\"_meta\\":null,\\"name\\":\\"simple_operation\\",\\"arguments\\":{\\"data\\":\\"json_test\\"}}", "payload_type": "CallToolRequestParams"}
INFO     fastmcp.structured:logging.py:LINE_NUMBER Completed message: {"event": "request_success", "timestamp": "2023-01-01T00:00:00+00:00", "method": "tools/call", "type": "request", "source": "client"}
DEBUG    fastmcp.fastmcp.server.server:server.py:LINE_NUMBER [LoggingTestServer] Handler called: list_tools

""")

    async def test_structured_logging_middleware_handles_errors(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test structured logging of errors with JSON format."""

        logging_middleware = StructuredLoggingMiddleware(methods=["tools/call"])
        logging_middleware._get_timestamp_from_context = (  # ty: ignore[invalid-assignment]
            lambda _: FIXED_DATE.isoformat()
        )

        logging_server.add_middleware(logging_middleware)

        with caplog_for_fastmcp(caplog):
            with caplog.at_level(logging.INFO):
                async with Client(logging_server) as client:
                    with pytest.raises(Exception):
                        await client.call_tool(
                            "operation_with_error", {"should_fail": True}
                        )

        # Verify that the structured logging middleware properly logs errors
        logs = caplog.text

        # The key assertion: structured logging middleware logged the error in JSON format
        assert re.search(
            r"fastmcp\.structured.*Failed message: tools/call.*Operation failed intentionally",
            logs,
        )

        # Verify the error contains expected error type and message
        assert "ValueError" in logs
        assert "Operation failed intentionally" in logs

    async def test_logging_middleware_with_different_operations(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test logging middleware with various MCP operations."""

        logging_server.add_middleware(
            LoggingMiddleware(
                methods=[
                    "tools/call",
                    "resources/list",
                    "prompts/get",
                    "resources/read",
                ]
            )
        )

        with caplog_for_fastmcp(caplog):
            async with Client(logging_server) as client:
                # Test different operation types
                await client.call_tool("simple_operation", {"data": "test"})
                await client.read_resource("log://test")
                await client.get_prompt("test_prompt")
                await client.list_resources()

        log_text = caplog.text

        # Should have logs for all different operation types
        # Note: Different operations may have different method names
        processing_count = log_text.count("Processing message:")
        completion_count = log_text.count("Completed message:")

        # Should have processed all 4 operations
        assert processing_count == 4
        assert completion_count == 4

    async def test_logging_middleware_custom_configuration(
        self, logging_server: FastMCP
    ):
        """Test logging middleware with custom logger configuration."""
        import io
        import logging

        # Create custom logger
        log_buffer = io.StringIO()
        handler = logging.StreamHandler(log_buffer)
        custom_logger = logging.getLogger("custom_logging_test")
        custom_logger.addHandler(handler)
        custom_logger.setLevel(logging.DEBUG)

        logging_server.add_middleware(
            LoggingMiddleware(
                logger=custom_logger,
                log_level=logging.DEBUG,
                include_payloads=True,
                methods=["tools/call"],
            )
        )

        async with Client(logging_server) as client:
            await client.call_tool("simple_operation", {"data": "custom_test"})

        # Check that our custom logger captured the logs
        log_output = log_buffer.getvalue()
        assert "Processing message:" in log_output
        assert "payload=" in log_output
