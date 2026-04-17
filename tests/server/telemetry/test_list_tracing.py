"""Tests for server-level OpenTelemetry tracing on list operations."""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from fastmcp import FastMCP


class TestListToolsTracing:
    async def test_list_tools_creates_span(self, trace_exporter: InMemorySpanExporter):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        tools = await mcp.list_tools()
        assert len(tools) == 1

        spans = trace_exporter.get_finished_spans()
        list_spans = [s for s in spans if s.name == "tools/list"]
        assert len(list_spans) >= 1

        span = list_spans[0]
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "tools/list"
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "tool"

    async def test_list_tools_empty_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        tools = await mcp.list_tools()
        assert len(tools) == 0

        spans = trace_exporter.get_finished_spans()
        list_spans = [s for s in spans if s.name == "tools/list"]
        assert len(list_spans) >= 1


class TestListResourcesTracing:
    async def test_list_resources_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.resource("config://app")
        def get_config() -> str:
            return "config"

        resources = await mcp.list_resources()
        assert len(resources) >= 1

        spans = trace_exporter.get_finished_spans()
        list_spans = [s for s in spans if s.name == "resources/list"]
        assert len(list_spans) >= 1

        span = list_spans[0]
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "resources/list"
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "resource"


class TestListResourceTemplatesTracing:
    async def test_list_resource_templates_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.resource("users://{user_id}/profile")
        def get_profile(user_id: str) -> str:
            return f"profile {user_id}"

        templates = await mcp.list_resource_templates()
        assert len(templates) >= 1

        spans = trace_exporter.get_finished_spans()
        list_spans = [s for s in spans if s.name == "resources/templates/list"]
        assert len(list_spans) >= 1

        span = list_spans[0]
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "resources/templates/list"
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "resource_template"


class TestListPromptsTracing:
    async def test_list_prompts_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.prompt()
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        prompts = await mcp.list_prompts()
        assert len(prompts) == 1

        spans = trace_exporter.get_finished_spans()
        list_spans = [s for s in spans if s.name == "prompts/list"]
        assert len(list_spans) >= 1

        span = list_spans[0]
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "prompts/list"
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "prompt"

    async def test_list_prompts_empty_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        prompts = await mcp.list_prompts()
        assert len(prompts) == 0

        spans = trace_exporter.get_finished_spans()
        list_spans = [s for s in spans if s.name == "prompts/list"]
        assert len(list_spans) >= 1
