"""Tests for client OpenTelemetry tracing on list operations."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from fastmcp import Client, FastMCP


class TestClientListToolsTracing:
    """Tests for client tools/list tracing."""

    async def test_list_tools_creates_client_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        server = FastMCP("test-server")

        @server.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        client = Client(server)
        async with client:
            tools = await client.list_tools()
            assert len(tools) == 1

        spans = trace_exporter.get_finished_spans()
        client_spans = [
            s
            for s in spans
            if s.name == "tools/list"
            and s.attributes is not None
            and "fastmcp.server.name" not in s.attributes
        ]
        assert len(client_spans) >= 1

        span = client_spans[0]
        assert span.kind == SpanKind.CLIENT
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "tools/list"

    async def test_list_tools_creates_both_client_and_server_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        server = FastMCP("test-server")

        @server.tool()
        def add(a: int, b: int) -> int:
            return a + b

        client = Client(server)
        async with client:
            await client.list_tools()

        spans = trace_exporter.get_finished_spans()
        tools_list_spans = [s for s in spans if s.name == "tools/list"]
        assert len(tools_list_spans) >= 2

        client_span = next(
            (
                s
                for s in tools_list_spans
                if s.attributes is not None
                and "fastmcp.server.name" not in s.attributes
            ),
            None,
        )
        server_span = next(
            (
                s
                for s in tools_list_spans
                if s.attributes is not None and "fastmcp.server.name" in s.attributes
            ),
            None,
        )

        assert client_span is not None, "Client should create a span"
        assert server_span is not None, "Server should create a span"
        assert client_span.kind == SpanKind.CLIENT
        assert server_span.kind == SpanKind.SERVER


class TestClientListResourcesTracing:
    """Tests for client resources/list tracing."""

    async def test_list_resources_creates_client_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        server = FastMCP("test-server")

        @server.resource("data://config")
        def get_config() -> str:
            return "config"

        client = Client(server)
        async with client:
            resources = await client.list_resources()
            assert len(resources) >= 1

        spans = trace_exporter.get_finished_spans()
        client_spans = [
            s
            for s in spans
            if s.name == "resources/list"
            and s.attributes is not None
            and "fastmcp.server.name" not in s.attributes
        ]
        assert len(client_spans) >= 1

        span = client_spans[0]
        assert span.kind == SpanKind.CLIENT
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "resources/list"


class TestClientListResourceTemplatesTracing:
    """Tests for client resources/templates/list tracing."""

    async def test_list_resource_templates_creates_client_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        server = FastMCP("test-server")

        @server.resource("users://{user_id}/profile")
        def get_profile(user_id: str) -> str:
            return f"profile {user_id}"

        client = Client(server)
        async with client:
            templates = await client.list_resource_templates()
            assert len(templates) >= 1

        spans = trace_exporter.get_finished_spans()
        client_spans = [
            s
            for s in spans
            if s.name == "resources/templates/list"
            and s.attributes is not None
            and "fastmcp.server.name" not in s.attributes
        ]
        assert len(client_spans) >= 1

        span = client_spans[0]
        assert span.kind == SpanKind.CLIENT
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "resources/templates/list"


class TestClientListPromptsTracing:
    """Tests for client prompts/list tracing."""

    async def test_list_prompts_creates_client_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        server = FastMCP("test-server")

        @server.prompt()
        def greeting() -> str:
            return "Hello!"

        client = Client(server)
        async with client:
            prompts = await client.list_prompts()
            assert len(prompts) == 1

        spans = trace_exporter.get_finished_spans()
        client_spans = [
            s
            for s in spans
            if s.name == "prompts/list"
            and s.attributes is not None
            and "fastmcp.server.name" not in s.attributes
        ]
        assert len(client_spans) >= 1

        span = client_spans[0]
        assert span.kind == SpanKind.CLIENT
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "prompts/list"


class TestClientListTraceContextPropagation:
    """List requests carry trace context in `_meta`, so server spans join the client trace."""

    @pytest.fixture
    def server(self) -> FastMCP:
        server = FastMCP("test-server")

        @server.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        @server.resource("data://config")
        def config() -> str:
            return "{}"

        @server.resource("users://{user_id}")
        def user(user_id: str) -> str:
            return user_id

        @server.prompt()
        def greeting() -> str:
            return "Hello!"

        return server

    @pytest.mark.parametrize(
        ("method", "span_name"),
        [
            ("list_tools", "tools/list"),
            ("list_resources", "resources/list"),
            ("list_resource_templates", "resources/templates/list"),
            ("list_prompts", "prompts/list"),
        ],
    )
    async def test_server_span_is_child_of_client_span(
        self,
        server: FastMCP,
        trace_exporter: InMemorySpanExporter,
        method: str,
        span_name: str,
    ):
        async with Client(server) as client:
            await getattr(client, method)()

        spans = [s for s in trace_exporter.get_finished_spans() if s.name == span_name]
        client_span = next(
            s
            for s in spans
            if s.kind == SpanKind.CLIENT
            and s.attributes is not None
            and "fastmcp.server.name" not in s.attributes
        )
        server_span = next(
            s
            for s in spans
            if s.attributes is not None and "fastmcp.server.name" in s.attributes
        )

        assert server_span.context.trace_id == client_span.context.trace_id
        assert server_span.parent is not None
        assert server_span.parent.span_id == client_span.context.span_id
