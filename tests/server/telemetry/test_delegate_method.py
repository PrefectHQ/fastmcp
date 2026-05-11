"""Tests for mcp.method.name attribute on delegate spans."""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fastmcp import FastMCP


class TestDelegateSpanMethod:
    """Tests that delegate spans include mcp.method.name."""

    async def test_mounted_tool_delegate_has_method(
        self, trace_exporter: InMemorySpanExporter
    ):
        child = FastMCP("child-server")

        @child.tool()
        def child_tool() -> str:
            return "result"

        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        await parent.call_tool("child_child_tool", {})

        spans = trace_exporter.get_finished_spans()
        delegate_span = next(
            (s for s in spans if s.name == "delegate child_tool"), None
        )
        assert delegate_span is not None
        assert delegate_span.attributes is not None
        assert delegate_span.attributes["mcp.method.name"] == "tools/call"

    async def test_mounted_resource_delegate_has_method(
        self, trace_exporter: InMemorySpanExporter
    ):
        child = FastMCP("child-server")

        @child.resource("data://config")
        def child_config() -> str:
            return "config data"

        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        await parent.read_resource("data://child/config")

        spans = trace_exporter.get_finished_spans()
        delegate_spans = [
            s
            for s in spans
            if s.name.startswith("delegate") and "data://config" in s.name
        ]
        assert len(delegate_spans) >= 1
        span = delegate_spans[0]
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "resources/read"

    async def test_mounted_prompt_delegate_has_method(
        self, trace_exporter: InMemorySpanExporter
    ):
        child = FastMCP("child-server")

        @child.prompt()
        def child_prompt() -> str:
            return "Hello from child!"

        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        await parent.render_prompt("child_child_prompt", {})

        spans = trace_exporter.get_finished_spans()
        delegate_span = next(
            (s for s in spans if s.name == "delegate child_prompt"), None
        )
        assert delegate_span is not None
        assert delegate_span.attributes is not None
        assert delegate_span.attributes["mcp.method.name"] == "prompts/get"
