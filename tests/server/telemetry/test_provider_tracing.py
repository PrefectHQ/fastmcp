"""Tests for provider-level OpenTelemetry tracing."""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fastmcp import FastMCP


class TestFastMCPProviderTracing:
    """Tests for FastMCPProvider delegation tracing."""

    async def test_mounted_tool_creates_delegate_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        # Create a child server with a tool
        child = FastMCP("child-server")

        @child.tool()
        def child_tool() -> str:
            return "child result"

        # Create parent server and mount child with namespace
        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        # Call the tool through the parent (namespace uses underscore, not slash)
        result = await parent.call_tool("child_child_tool", {})
        assert "child result" in str(result)

        # Check spans: should have parent tool span, delegate span, and child tool span
        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # Parent server creates "tools/call child_child_tool"
        assert "tools/call child_child_tool" in span_names
        # FastMCPProvider creates "delegate child_tool"
        assert "delegate child_tool" in span_names
        # Child server creates "tools/call child_tool"
        assert "tools/call child_tool" in span_names

        # Verify delegate span has correct attributes
        delegate_span = next(s for s in spans if s.name == "delegate child_tool")
        assert delegate_span.attributes["fastmcp.provider.type"] == "FastMCPProvider"
        assert delegate_span.attributes["fastmcp.component.key"] == "child_tool"

    async def test_mounted_resource_creates_delegate_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        # Create a child server with a resource
        child = FastMCP("child-server")

        @child.resource("data://config")
        def child_config() -> str:
            return "config data"

        # Create parent server and mount child with namespace
        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        # Read the resource through the parent (namespace is in the URI path)
        result = await parent.read_resource("data://child/config")
        assert "config data" in str(result)

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # Should have delegate span for resource
        assert any(
            "delegate" in name and "data://config" in name for name in span_names
        )

    async def test_mounted_prompt_creates_delegate_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        # Create a child server with a prompt
        child = FastMCP("child-server")

        @child.prompt()
        def child_prompt() -> str:
            return "Hello from child!"

        # Create parent server and mount child with namespace
        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        # Render the prompt through the parent (namespace uses underscore)
        result = await parent.render_prompt("child_child_prompt", {})
        assert "Hello from child!" in str(result)

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # Should have delegate span for prompt
        assert "delegate child_prompt" in span_names

        # Verify delegate span has correct attributes
        delegate_span = next(s for s in spans if s.name == "delegate child_prompt")
        assert delegate_span.attributes["fastmcp.provider.type"] == "FastMCPProvider"


class TestProviderSpanHierarchy:
    """Tests for span parent-child relationships in mounted servers."""

    async def test_delegate_span_is_child_of_server_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        # Create nested server structure
        child = FastMCP("child")

        @child.tool()
        def greet() -> str:
            return "Hello"

        parent = FastMCP("parent")
        parent.mount(child, namespace="ns")

        await parent.call_tool("ns_greet", {})

        spans = trace_exporter.get_finished_spans()

        # Find the spans
        parent_span = next(s for s in spans if s.name == "tools/call ns_greet")
        delegate_span = next(s for s in spans if s.name == "delegate greet")
        child_span = next(s for s in spans if s.name == "tools/call greet")

        # Verify parent-child relationships
        assert delegate_span.parent.span_id == parent_span.context.span_id
        assert child_span.parent.span_id == delegate_span.context.span_id
