"""Tests for FASTMCP_TELEMETRY_OPT_OUT flag."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import INVALID_SPAN

from fastmcp import Client, FastMCP
from fastmcp.client.telemetry import client_span
from fastmcp.server.telemetry import delegate_span, server_span
from fastmcp.telemetry import (
    inject_trace_context,
    is_telemetry_opted_out,
)


class TestIsTelemetryOptedOut:
    def test_default_is_not_opted_out(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("FASTMCP_TELEMETRY_OPT_OUT", raising=False)
        assert is_telemetry_opted_out() is False

    @pytest.mark.parametrize("value", ["YES", "yes", "Yes", "true", "TRUE", "1"])
    def test_truthy_values_opt_out(self, monkeypatch: pytest.MonkeyPatch, value: str):
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", value)
        assert is_telemetry_opted_out() is True

    @pytest.mark.parametrize("value", ["no", "false", "0", "", "maybe"])
    def test_non_truthy_values_do_not_opt_out(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ):
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", value)
        assert is_telemetry_opted_out() is False

    def test_whitespace_is_stripped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", "  yes  ")
        assert is_telemetry_opted_out() is True


class TestClientSpanOptOut:
    def test_yields_invalid_span(
        self,
        monkeypatch: pytest.MonkeyPatch,
        trace_exporter: InMemorySpanExporter,
    ):
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", "YES")
        with client_span(
            "tools/call test", method="tools/call", component_key="test"
        ) as span:
            assert span is INVALID_SPAN

        assert len(trace_exporter.get_finished_spans()) == 0

    def test_does_not_modify_active_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        trace_exporter: InMemorySpanExporter,
    ):
        """Opted-out client_span must not replace the active span context."""
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", "YES")
        current_before = trace.get_current_span()
        with client_span("tools/call test", method="tools/call", component_key="test"):
            current_inside = trace.get_current_span()
        assert current_inside is current_before


class TestServerSpanOptOut:
    def test_yields_invalid_span(
        self,
        monkeypatch: pytest.MonkeyPatch,
        trace_exporter: InMemorySpanExporter,
    ):
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", "YES")
        with server_span(
            "tools/call test",
            method="tools/call",
            server_name="test",
            component_type="tool",
            component_key="test",
        ) as span:
            assert span is INVALID_SPAN

        assert len(trace_exporter.get_finished_spans()) == 0

    def test_does_not_modify_active_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        trace_exporter: InMemorySpanExporter,
    ):
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", "YES")
        current_before = trace.get_current_span()
        with server_span(
            "tools/call test",
            method="tools/call",
            server_name="test",
            component_type="tool",
            component_key="test",
        ):
            current_inside = trace.get_current_span()
        assert current_inside is current_before


class TestDelegateSpanOptOut:
    def test_yields_invalid_span(
        self,
        monkeypatch: pytest.MonkeyPatch,
        trace_exporter: InMemorySpanExporter,
    ):
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", "YES")
        with delegate_span(
            "test", provider_type="linked", component_key="test"
        ) as span:
            assert span is INVALID_SPAN

        assert len(trace_exporter.get_finished_spans()) == 0


class TestContextPropagationStillWorks:
    def test_inject_works_when_opted_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
        trace_exporter: InMemorySpanExporter,
    ):
        """inject_trace_context should still work when telemetry is opted out,
        because external instrumentations need context propagation."""
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", "YES")

        from fastmcp.telemetry import get_tracer

        tracer = get_tracer()
        with tracer.start_as_current_span("external-parent"):
            meta = inject_trace_context()

        assert meta is not None
        assert "traceparent" in meta


class TestEndToEndOptOut:
    async def test_no_spans_when_opted_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
        trace_exporter: InMemorySpanExporter,
    ):
        """No FastMCP spans should be created when telemetry is opted out."""
        monkeypatch.setenv("FASTMCP_TELEMETRY_OPT_OUT", "YES")

        server = FastMCP("test-server")

        @server.tool()
        def echo(message: str) -> str:
            return message

        client = Client(server)
        async with client:
            result = await client.call_tool("echo", {"message": "hello"})
            assert "hello" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 0, (
            f"Expected no spans when opted out, got: {[s.name for s in spans]}"
        )
