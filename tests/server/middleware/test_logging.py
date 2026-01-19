"""Tests for logging middleware."""

import datetime
import logging
from collections.abc import Generator
from typing import Any, Literal, TypeVar
from unittest.mock import AsyncMock, MagicMock, patch

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

FIXED_DATE = datetime.datetime(2023, 1, 1, tzinfo=datetime.timezone.utc)

T = TypeVar("T")


def get_log_lines(
    caplog: pytest.LogCaptureFixture, module: str | None = None
) -> list[str]:
    """Get log lines from a caplog fixture."""
    return [
        record.message
        for record in caplog.records
        if (module or "logging") in record.name
    ]


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


@pytest.fixture(autouse=True)
def mock_duration_ms() -> Generator[float, None]:
    """Mock duration_ms."""
    patched = patch(
        "fastmcp.server.middleware.logging._get_duration_ms", return_value=0.02
    )
    patched.start()
    yield
    patched.stop()


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
        middleware = StructuredLoggingMiddleware()

        assert middleware.logger.name == "fastmcp.middleware.structured_logging"
        assert middleware.log_level == logging.INFO
        assert middleware.include_payloads is False
        assert middleware.include_payload_length is False
        assert middleware.estimate_payload_tokens is False
        assert middleware.structured_logging is True

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

            message = middleware._create_before_message(mock_context)

            assert message == snapshot(
                {
                    "event": "request_start",
                    "source": "client",
                    "method": "test_method",
                }
            )

        def test_create_message_with_payloads(
            self, mock_context: MiddlewareContext[Any]
        ):
            """Test message formatting with payloads."""
            middleware = StructuredLoggingMiddleware(include_payloads=True)

            message = middleware._create_before_message(mock_context)

            assert message == snapshot(
                {
                    "event": "request_start",
                    "source": "client",
                    "method": "test_method",
                    "payload": '{"method":"tools/call","params":{"task":null,"_meta":null,"name":"test_method","arguments":{"param":"value"}}}',
                    "payload_type": "CallToolRequest",
                }
            )

        def test_calculate_response_size(self, mock_context: MiddlewareContext[Any]):
            """Test response size calculation."""
            middleware = StructuredLoggingMiddleware(include_payload_length=True)
            message = middleware._create_before_message(mock_context)

            assert message == snapshot(
                {
                    "event": "request_start",
                    "source": "client",
                    "method": "test_method",
                    "payload_length": 110,
                }
            )

        def test_calculate_response_size_with_token_estimation(
            self, mock_context: MiddlewareContext[Any]
        ):
            """Test response size calculation with token estimation."""
            middleware = StructuredLoggingMiddleware(
                include_payload_length=True, estimate_payload_tokens=True
            )
            message = middleware._create_before_message(mock_context)

            assert message == snapshot(
                {
                    "event": "request_start",
                    "source": "client",
                    "method": "test_method",
                    "payload_tokens": 27,
                    "payload_length": 110,
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

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"
        assert mock_call_next.called

        assert get_log_lines(caplog) == snapshot(
            [
                '{"event": "request_start", "method": "test_method", "source": "client"}',
                '{"event": "request_success", "method": "test_method", "source": "client", "duration_ms": 0.02}',
            ]
        )

    async def test_on_message_failure(
        self, mock_context: MiddlewareContext[Any], caplog: pytest.LogCaptureFixture
    ):
        """Test logging failed messages."""
        middleware = StructuredLoggingMiddleware()
        mock_call_next = AsyncMock(side_effect=ValueError("test error"))

        with pytest.raises(ValueError):
            await middleware.on_message(mock_context, mock_call_next)

        assert get_log_lines(caplog) == snapshot(
            [
                '{"event": "request_start", "method": "test_method", "source": "client"}',
                '{"event": "request_error", "method": "test_method", "source": "client", "duration_ms": 0.02, "error": "test error"}',
            ]
        )


class TestLoggingMiddleware:
    """Test structured logging middleware functionality."""

    def test_init_default(self):
        """Test default initialization."""
        middleware = LoggingMiddleware()
        assert middleware.logger.name == "fastmcp.middleware.logging"
        assert middleware.log_level == logging.INFO
        assert middleware.include_payloads is False
        assert middleware.include_payload_length is False
        assert middleware.estimate_payload_tokens is False

    def test_format_message(self, mock_context: MiddlewareContext[Any]):
        """Test message formatting."""
        middleware = LoggingMiddleware()
        message = middleware._create_before_message(mock_context)
        formatted = middleware._format_message(message)

        assert formatted == snapshot(
            "event=request_start method=test_method source=client"
        )

    def test_create_before_message_long_payload(
        self, mock_context: MiddlewareContext[Any]
    ):
        """Test message formatting with long payload truncation."""
        middleware = LoggingMiddleware(include_payloads=True, max_payload_length=10)

        message = middleware._create_before_message(mock_context)

        formatted = middleware._format_message(message)

        assert formatted == snapshot(
            'event=request_start method=test_method source=client payload={"method":... payload_type=CallToolRequest'
        )

    async def test_on_message_failure(
        self, mock_context: MiddlewareContext[Any], caplog: pytest.LogCaptureFixture
    ):
        """Test structured logging of failed messages."""
        middleware = StructuredLoggingMiddleware()
        mock_call_next = AsyncMock(side_effect=ValueError("test error"))

        with pytest.raises(ValueError):
            await middleware.on_message(mock_context, mock_call_next)

        # Check that we have structured JSON logs
        assert get_log_lines(caplog) == snapshot(
            [
                '{"event": "request_start", "method": "test_method", "source": "client"}',
                '{"event": "request_error", "method": "test_method", "source": "client", "duration_ms": 0.02, "error": "test error"}',
            ]
        )

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

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"

        assert get_log_lines(caplog) == snapshot(
            [
                '{"event": "request_start", "method": "test_method", "source": "client", "payload": "{\\"method\\":\\"resources/read\\",\\"params\\":{\\"task\\":null,\\"_meta\\":null,\\"uri\\":\\"test://example/1\\"}}", "payload_type": "ReadResourceRequest"}',
                '{"event": "request_success", "method": "test_method", "source": "client", "duration_ms": 0.02}',
            ]
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

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"

        assert get_log_lines(caplog) == snapshot(
            [
                '{"event": "request_start", "method": "test_method", "source": "client", "payload": "{\\"name\\":\\"tmpl\\",\\"version\\":null,\\"title\\":null,\\"description\\":null,\\"icons\\":null,\\"tags\\":[],\\"meta\\":null,\\"task_config\\":{\\"mode\\":\\"forbidden\\",\\"poll_interval\\":\\"PT5S\\"},\\"uri_template\\":\\"tmpl://{id}\\",\\"mime_type\\":\\"text/plain\\",\\"parameters\\":{\\"id\\":{\\"type\\":\\"string\\"}},\\"annotations\\":null}", "payload_type": "ResourceTemplate"}',
                '{"event": "request_success", "method": "test_method", "source": "client", "duration_ms": 0.02}',
            ]
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

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"

        assert get_log_lines(caplog) == snapshot(
            [
                '{"event": "request_start", "method": "test_method", "source": "client", "payload": "{\\"method\\":\\"tools/call\\",\\"params\\":{\\"task\\":null,\\"_meta\\":null,\\"name\\":\\"test_method\\",\\"arguments\\":{\\"obj\\":\\"NON_SERIALIZABLE\\"}}}", "payload_type": "CallToolRequest"}',
                '{"event": "request_success", "method": "test_method", "source": "client", "duration_ms": 0.02}',
            ]
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

        result = await middleware.on_message(mock_context, mock_call_next)

        assert result == "test_result"

        assert get_log_lines(caplog) == snapshot(
            [
                '{"event": "request_start", "method": "test_method", "source": "client", "payload": "CUSTOM_PAYLOAD", "payload_type": "CallToolRequest"}',
                '{"event": "request_success", "method": "test_method", "source": "client", "duration_ms": 0.02}',
            ]
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

        logging_server.add_middleware(logging_middleware)

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
        assert get_log_lines(caplog) == snapshot(
            [
                "event=request_start method=tools/call source=client",
                "event=request_success method=tools/call source=client duration_ms=0.02",
                "event=request_start method=tools/call source=client",
                "event=request_success method=tools/call source=client duration_ms=0.02",
            ]
        )

    async def test_logging_middleware_logs_failures(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test that logging middleware captures failed operations."""
        logging_server.add_middleware(LoggingMiddleware(methods=["tools/call"]))

        async with Client(logging_server) as client:
            # This should fail and be logged
            with pytest.raises(Exception):
                await client.call_tool("operation_with_error", {"should_fail": True})

        log_text = caplog.text

        # Should have processing and failure logs
        assert log_text.splitlines()[-1] == snapshot(
            "ERROR    fastmcp.middleware.logging:logging.py:122 event=request_error method=tools/call source=client duration_ms=0.02 error=Error calling tool 'operation_with_error': Operation failed intentionally"
        )

    async def test_logging_middleware_with_payloads(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test logging middleware when configured to include payloads."""

        middleware = LoggingMiddleware(
            include_payloads=True, max_payload_length=500, methods=["tools/call"]
        )
        logging_server.add_middleware(middleware)

        async with Client(logging_server) as client:
            await client.call_tool("simple_operation", {"data": "payload_test"})

        assert get_log_lines(caplog) == snapshot(
            [
                'event=request_start method=tools/call source=client payload={"task":null,"_meta":null,"name":"simple_operation","arguments":{"data":"payload_test"}} payload_type=CallToolRequestParams',
                "event=request_success method=tools/call source=client duration_ms=0.02",
            ]
        )

    async def test_structured_logging_middleware_produces_json(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test that structured logging middleware produces parseable JSON logs."""

        logging_middleware = StructuredLoggingMiddleware(
            include_payloads=True, methods=["tools/call"]
        )

        logging_server.add_middleware(logging_middleware)

        async with Client(logging_server) as client:
            await client.call_tool(
                name="simple_operation", arguments={"data": "json_test"}
            )

        assert get_log_lines(caplog) == snapshot(
            [
                '{"event": "request_start", "method": "tools/call", "source": "client", "payload": "{\\"task\\":null,\\"_meta\\":null,\\"name\\":\\"simple_operation\\",\\"arguments\\":{\\"data\\":\\"json_test\\"}}", "payload_type": "CallToolRequestParams"}',
                '{"event": "request_success", "method": "tools/call", "source": "client", "duration_ms": 0.02}',
            ]
        )

    async def test_structured_logging_middleware_handles_errors(
        self, logging_server: FastMCP, caplog: pytest.LogCaptureFixture
    ):
        """Test structured logging of errors with JSON format."""

        logging_middleware = StructuredLoggingMiddleware(methods=["tools/call"])

        logging_server.add_middleware(logging_middleware)

        with caplog.at_level(logging.INFO):
            async with Client(logging_server) as client:
                with pytest.raises(Exception):
                    await client.call_tool(
                        "operation_with_error", {"should_fail": True}
                    )

        assert get_log_lines(caplog) == snapshot(
            [
                '{"event": "request_start", "method": "tools/call", "source": "client"}',
                '{"event": "request_error", "method": "tools/call", "source": "client", "duration_ms": 0.02, "error": "Error calling tool \'operation_with_error\': Operation failed intentionally"}',
            ]
        )

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

        async with Client(logging_server) as client:
            # Test different operation types
            await client.call_tool("simple_operation", {"data": "test"})
            await client.read_resource("log://test")
            await client.get_prompt("test_prompt")
            await client.list_resources()

        assert get_log_lines(caplog) == snapshot(
            [
                "event=request_start method=tools/call source=client",
                "event=request_success method=tools/call source=client duration_ms=0.02",
                "event=request_start method=resources/read source=client",
                "event=request_success method=resources/read source=client duration_ms=0.02",
                "event=request_start method=prompts/get source=client",
                "event=request_success method=prompts/get source=client duration_ms=0.02",
                "event=request_start method=resources/list source=client",
                "event=request_success method=resources/list source=client duration_ms=0.02",
            ]
        )

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
        assert log_output == snapshot("""\
event=request_start method=tools/call source=client payload={"task":null,"_meta":null,"name":"simple_operation","arguments":{"data":"custom_test"}} payload_type=CallToolRequestParams
event=request_success method=tools/call source=client duration_ms=0.02
""")
