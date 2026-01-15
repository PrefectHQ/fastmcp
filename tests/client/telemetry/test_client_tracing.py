"""Tests for client OpenTelemetry tracing."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError


class TestClientToolTracing:
    """Tests for client tool call tracing."""

    async def test_call_tool_creates_span(self, trace_exporter: InMemorySpanExporter):
        server = FastMCP("test-server")

        @server.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        client = Client(server)
        async with client:
            result = await client.call_tool("greet", {"name": "World"})
            assert "Hello, World!" in str(result)

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # Client should create "tools/call greet" span
        assert "tools/call greet" in span_names

    async def test_call_tool_span_attributes(
        self, trace_exporter: InMemorySpanExporter
    ):
        server = FastMCP("test-server")

        @server.tool()
        def add(a: int, b: int) -> int:
            return a + b

        client = Client(server)
        async with client:
            await client.call_tool("add", {"a": 1, "b": 2})

        spans = trace_exporter.get_finished_spans()

        # Find client-side span (doesn't have fastmcp.server.name)
        client_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call add"
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        assert client_span is not None
        # Standard MCP semantic conventions
        assert client_span.attributes["mcp.method.name"] == "tools/call"
        # Standard RPC semantic conventions
        assert client_span.attributes["rpc.system"] == "mcp"
        assert client_span.attributes["rpc.method"] == "tools/call"
        # FastMCP-specific attributes
        assert client_span.attributes["fastmcp.component.key"] == "add"


class TestClientResourceTracing:
    """Tests for client resource read tracing."""

    async def test_read_resource_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        server = FastMCP("test-server")

        @server.resource("data://config")
        def get_config() -> str:
            return "config data"

        client = Client(server)
        async with client:
            result = await client.read_resource("data://config")
            assert "config data" in str(result)

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # Client should create "resources/read data://config" span
        assert "resources/read data://config" in span_names

    async def test_read_resource_span_attributes(
        self, trace_exporter: InMemorySpanExporter
    ):
        server = FastMCP("test-server")

        @server.resource("data://config")
        def get_config() -> str:
            return "config value"

        client = Client(server)
        async with client:
            await client.read_resource("data://config")

        spans = trace_exporter.get_finished_spans()

        # Find client-side resource span (doesn't have fastmcp.server.name)
        client_span = next(
            (
                s
                for s in spans
                if s.name.startswith("resources/read data://")
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        assert client_span is not None
        # Standard MCP semantic conventions
        assert client_span.attributes["mcp.method.name"] == "resources/read"
        assert "data://" in str(client_span.attributes["mcp.resource.uri"])
        # Standard RPC semantic conventions
        assert client_span.attributes["rpc.system"] == "mcp"
        assert client_span.attributes["rpc.method"] == "resources/read"
        # FastMCP-specific attributes
        # The URI may be normalized with trailing slash
        assert "data://" in str(client_span.attributes["fastmcp.component.key"])


class TestClientPromptTracing:
    """Tests for client prompt get tracing."""

    async def test_get_prompt_creates_span(self, trace_exporter: InMemorySpanExporter):
        server = FastMCP("test-server")

        @server.prompt()
        def greeting() -> str:
            return "Hello from prompt!"

        client = Client(server)
        async with client:
            result = await client.get_prompt("greeting")
            assert "Hello from prompt!" in str(result)

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # Client should create "prompts/get greeting" span
        assert "prompts/get greeting" in span_names

    async def test_get_prompt_span_attributes(
        self, trace_exporter: InMemorySpanExporter
    ):
        server = FastMCP("test-server")

        @server.prompt()
        def welcome(name: str) -> str:
            return f"Welcome, {name}!"

        client = Client(server)
        async with client:
            await client.get_prompt("welcome", {"name": "Test"})

        spans = trace_exporter.get_finished_spans()

        # Find client-side prompt span (doesn't have fastmcp.server.name)
        client_span = next(
            (
                s
                for s in spans
                if s.name == "prompts/get welcome"
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        assert client_span is not None
        # Standard MCP semantic conventions
        assert client_span.attributes["mcp.method.name"] == "prompts/get"
        # Standard RPC semantic conventions
        assert client_span.attributes["rpc.system"] == "mcp"
        assert client_span.attributes["rpc.method"] == "prompts/get"
        # FastMCP-specific attributes
        assert client_span.attributes["fastmcp.component.key"] == "welcome"


class TestClientServerSpanHierarchy:
    """Tests for span relationships between client and server."""

    async def test_client_and_server_spans_created(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Both client and server should create spans for the same operation."""
        server = FastMCP("test-server")

        @server.tool()
        def echo(message: str) -> str:
            return message

        client = Client(server)
        async with client:
            await client.call_tool("echo", {"message": "test"})

        spans = trace_exporter.get_finished_spans()

        # Find client span (no fastmcp.server.name) and server span (has fastmcp.server.name)
        client_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call echo"
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        server_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call echo"
                and s.attributes is not None
                and "fastmcp.server.name" in s.attributes
            ),
            None,
        )

        # Both spans should exist
        assert client_span is not None, "Client should create a span"
        assert server_span is not None, "Server should create a span"

        # Verify span kinds are correct
        assert client_span.kind == SpanKind.CLIENT, "Client span should be CLIENT kind"
        assert server_span.kind == SpanKind.SERVER, "Server span should be SERVER kind"

        # Verify the spans have different characteristics
        assert client_span.attributes["rpc.method"] == "tools/call"
        assert server_span.attributes["fastmcp.server.name"] == "test-server"

    async def test_trace_context_propagation(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Server span should be a child of client span via trace context propagation."""
        server = FastMCP("test-server")

        @server.tool()
        def add(a: int, b: int) -> int:
            return a + b

        client = Client(server)
        async with client:
            await client.call_tool("add", {"a": 1, "b": 2})

        spans = trace_exporter.get_finished_spans()

        # Find client span and server span
        client_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call add"
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        server_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call add"
                and s.attributes is not None
                and "fastmcp.server.name" in s.attributes
            ),
            None,
        )

        assert client_span is not None, "Client span should exist"
        assert server_span is not None, "Server span should exist"

        # Verify trace context propagation: server span should be child of client span
        # Both should share the same trace_id
        assert server_span.context.trace_id == client_span.context.trace_id, (
            "Server and client spans should share the same trace_id"
        )

        # Server span's parent should be the client span
        assert server_span.parent is not None, "Server span should have a parent"
        assert server_span.parent.span_id == client_span.context.span_id, (
            "Server span's parent should be the client span"
        )


class TestClientErrorTracing:
    """Tests for client span creation during errors.

    Note: MCP protocol errors are returned as successful responses with error content,
    so client spans may not have ERROR status even when the operation fails. This is
    different from server-side where exceptions happen inside the span.

    The server-side span WILL have ERROR status because the exception occurs within
    the server's span context. The client span represents the successful MCP protocol
    round-trip, while application-level errors are communicated via the response.
    """

    async def test_call_tool_error_creates_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Both client and server spans should be created when tool fails."""
        server = FastMCP("test-server")

        @server.tool()
        def failing_tool() -> str:
            raise ValueError("Something went wrong")

        client = Client(server)
        async with client:
            with pytest.raises(ToolError):
                await client.call_tool("failing_tool", {})

        spans = trace_exporter.get_finished_spans()

        # Find client-side span
        client_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call failing_tool"
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        # Find server-side span
        server_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call failing_tool"
                and s.attributes is not None
                and "fastmcp.server.name" in s.attributes
            ),
            None,
        )

        # Both spans should exist
        assert client_span is not None, "Client should create a span"
        assert server_span is not None, "Server should create a span"

        # Server span should have ERROR status (exception inside span)
        assert server_span.status.status_code == StatusCode.ERROR

    async def test_read_resource_error_creates_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Both client and server spans should be created when resource read fails."""
        server = FastMCP("test-server")

        @server.resource("data://fail")
        def failing_resource() -> str:
            raise ValueError("Resource error")

        client = Client(server)
        async with client:
            with pytest.raises(Exception):
                await client.read_resource("data://fail")

        spans = trace_exporter.get_finished_spans()

        # Find client-side span
        client_span = next(
            (
                s
                for s in spans
                if s.name.startswith("resources/read data://fail")
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        # Find server-side span
        server_span = next(
            (
                s
                for s in spans
                if s.name.startswith("resources/read data://fail")
                and s.attributes is not None
                and "fastmcp.server.name" in s.attributes
            ),
            None,
        )

        # Both spans should exist
        assert client_span is not None, "Client should create a span"
        assert server_span is not None, "Server should create a span"

        # Server span should have ERROR status
        assert server_span.status.status_code == StatusCode.ERROR

    async def test_get_prompt_error_creates_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Both client and server spans should be created when prompt get fails."""
        server = FastMCP("test-server")

        @server.prompt()
        def failing_prompt() -> str:
            raise ValueError("Prompt error")

        client = Client(server)
        async with client:
            with pytest.raises(Exception):
                await client.get_prompt("failing_prompt", {})

        spans = trace_exporter.get_finished_spans()

        # Find client-side span
        client_span = next(
            (
                s
                for s in spans
                if s.name == "prompts/get failing_prompt"
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        # Find server-side span
        server_span = next(
            (
                s
                for s in spans
                if s.name == "prompts/get failing_prompt"
                and s.attributes is not None
                and "fastmcp.server.name" in s.attributes
            ),
            None,
        )

        # Both spans should exist
        assert client_span is not None, "Client should create a span"
        assert server_span is not None, "Server should create a span"

        # Server span should have ERROR status
        assert server_span.status.status_code == StatusCode.ERROR

    async def test_call_nonexistent_tool_creates_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Both client and server spans should be created for nonexistent tool."""
        server = FastMCP("test-server")

        client = Client(server)
        async with client:
            with pytest.raises(Exception):
                await client.call_tool("nonexistent", {})

        spans = trace_exporter.get_finished_spans()

        # Find client-side span
        client_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call nonexistent"
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        # Find server-side span
        server_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call nonexistent"
                and s.attributes is not None
                and "fastmcp.server.name" in s.attributes
            ),
            None,
        )

        # Both spans should exist
        assert client_span is not None, "Client should create a span"
        assert server_span is not None, "Server should create a span"

        # Server span should have ERROR status
        assert server_span.status.status_code == StatusCode.ERROR


class TestSessionIdOnSpans:
    """Tests for session ID capture on client and server spans.

    Session IDs are only available with HTTP transport (StreamableHttp).
    """

    @pytest.fixture
    async def http_server_url(self) -> AsyncGenerator[str, None]:
        """Start an HTTP server and return its URL."""
        from fastmcp.utilities.tests import run_server_async

        server = FastMCP("session-test-server")

        @server.tool()
        def echo(message: str) -> str:
            return message

        async with run_server_async(server) as url:
            yield url

    async def test_client_span_includes_session_id(
        self,
        trace_exporter: InMemorySpanExporter,
        http_server_url: str,
    ):
        """Client span should include session ID when using HTTP transport."""
        from fastmcp.client.transports import StreamableHttpTransport

        transport = StreamableHttpTransport(http_server_url)
        client = Client(transport=transport)
        async with client:
            await client.call_tool("echo", {"message": "test"})

        spans = trace_exporter.get_finished_spans()

        # Find client-side span
        client_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call echo"
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )

        assert client_span is not None, "Client should create a span"
        assert "mcp.session.id" in client_span.attributes
        assert client_span.attributes["mcp.session.id"] is not None

    async def test_server_span_includes_session_id(
        self,
        trace_exporter: InMemorySpanExporter,
        http_server_url: str,
    ):
        """Server span should include session ID when called via HTTP."""
        from fastmcp.client.transports import StreamableHttpTransport

        transport = StreamableHttpTransport(http_server_url)
        client = Client(transport=transport)
        async with client:
            await client.call_tool("echo", {"message": "test"})

        spans = trace_exporter.get_finished_spans()

        # Find server-side span
        server_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call echo"
                and s.attributes is not None
                and "fastmcp.server.name" in s.attributes
            ),
            None,
        )

        assert server_span is not None, "Server should create a span"
        assert "mcp.session.id" in server_span.attributes
        assert server_span.attributes["mcp.session.id"] is not None

    async def test_client_and_server_share_same_session_id(
        self,
        trace_exporter: InMemorySpanExporter,
        http_server_url: str,
    ):
        """Client and server spans should have the same session ID."""
        from fastmcp.client.transports import StreamableHttpTransport

        transport = StreamableHttpTransport(http_server_url)
        client = Client(transport=transport)
        async with client:
            await client.call_tool("echo", {"message": "test"})

        spans = trace_exporter.get_finished_spans()

        # Find both spans
        client_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call echo"
                and s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        server_span = next(
            (
                s
                for s in spans
                if s.name == "tools/call echo"
                and s.attributes is not None
                and "fastmcp.server.name" in s.attributes
            ),
            None,
        )

        assert client_span is not None
        assert server_span is not None

        # Both should have session IDs and they should match
        client_session = client_span.attributes.get("mcp.session.id")
        server_session = server_span.attributes.get("mcp.session.id")

        assert client_session is not None, "Client span should have session ID"
        assert server_session is not None, "Server span should have session ID"
        assert client_session == server_session, (
            "Client and server should share the same session ID"
        )
