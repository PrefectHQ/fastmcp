"""Tests for FastMCP telemetry interoperability modes."""

from __future__ import annotations

from typing import Any, cast

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from mcp.server.lowlevel.server import request_ctx

from fastmcp import Client, FastMCP
from fastmcp.client.telemetry import client_span
from fastmcp.server.telemetry import server_span
from fastmcp.telemetry import inject_trace_context, suppress_fastmcp_telemetry
from fastmcp.utilities.tests import temporary_settings


class DummyReqCtx:
    """Minimal request context for server telemetry tests."""

    def __init__(self, meta: dict[str, str]):
        self.meta = meta


class TestClientInteropMode:
    async def test_propagation_only_mode_preserves_outer_client_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        with temporary_settings(telemetry_mode="propagation_only"):
            tracer = trace.get_tracer("external")
            with tracer.start_as_current_span("external-client-parent") as parent_span:
                with client_span(
                    "tools/call weather",
                    "tools/call",
                    "weather",
                    tool_name="weather",
                ) as span:
                    meta = inject_trace_context()
                    assert not span.is_recording()

            spans = trace_exporter.get_finished_spans()
            assert [span.name for span in spans] == ["external-client-parent"]
            assert meta is not None
            assert meta["traceparent"].split("-")[2] == format(
                parent_span.get_span_context().span_id, "016x"
            )

    async def test_context_manager_suppresses_only_fastmcp_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        tracer = trace.get_tracer("external")
        with tracer.start_as_current_span("external-client-parent") as parent_span:
            with suppress_fastmcp_telemetry():
                with client_span(
                    "tools/call weather",
                    "tools/call",
                    "weather",
                    tool_name="weather",
                ) as span:
                    meta = inject_trace_context()
                    assert not span.is_recording()

        spans = trace_exporter.get_finished_spans()
        assert [span.name for span in spans] == ["external-client-parent"]
        assert meta is not None
        assert meta["traceparent"].split("-")[2] == format(
            parent_span.get_span_context().span_id, "016x"
        )

    async def test_end_to_end_propagation_only_suppresses_native_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        child = FastMCP("child-server")

        @child.tool()
        def child_tool() -> str:
            return "child result"

        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        with temporary_settings(telemetry_mode="propagation_only"):
            tracer = trace.get_tracer("external")
            with tracer.start_as_current_span("external-request"):
                client = Client(parent)
                async with client:
                    result = await client.call_tool("child_child_tool", {})
                    assert "child result" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert [span.name for span in spans] == ["external-request"]


class TestServerInteropMode:
    async def test_server_span_uses_meta_parent_and_links_ambient_context(
        self,
        monkeypatch,
        trace_exporter: InMemorySpanExporter,
    ):
        import fastmcp.server.telemetry as server_telemetry

        monkeypatch.setattr(server_telemetry, "get_auth_span_attributes", lambda: {})
        monkeypatch.setattr(server_telemetry, "get_session_span_attributes", lambda: {})

        tracer = trace.get_tracer("external")

        remote_parent = tracer.start_span("external-client-parent")
        token = otel_context.attach(trace.set_span_in_context(remote_parent))
        try:
            meta = inject_trace_context()
        finally:
            otel_context.detach(token)

        with tracer.start_as_current_span("ambient-http-request") as ambient_span:
            req_token = request_ctx.set(cast(Any, DummyReqCtx(meta or {})))
            try:
                with server_span(
                    "tools/call weather",
                    "tools/call",
                    "test-server",
                    "tool",
                    "weather",
                    tool_name="weather",
                ):
                    pass
            finally:
                request_ctx.reset(req_token)

        remote_parent.end()

        spans = {
            span.name: span
            for span in trace_exporter.get_finished_spans()
            if span.name
            in {"external-client-parent", "ambient-http-request", "tools/call weather"}
        }
        server_span_export = spans["tools/call weather"]

        assert server_span_export.parent is not None
        assert server_span_export.parent.span_id == remote_parent.get_span_context().span_id
        assert any(
            link.context.span_id == ambient_span.get_span_context().span_id
            for link in server_span_export.links
        )
