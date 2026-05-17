"""Tests for telemetry interoperability mode.

Validates that native FastMCP spans can be suppressed while context
propagation continues to work.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock, patch

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import INVALID_SPAN

import fastmcp
from fastmcp.telemetry import (
    get_noop_span,
    native_telemetry_enabled,
    suppress_fastmcp_telemetry,
)

# A fixed W3C traceparent: 00-<trace_id>-<span_id>-<flags>
_INCOMING_TRACE_ID_HEX = "4bf92f3577b34da6a3ce929d0e0e4736"
_INCOMING_TRACEPARENT = f"00-{_INCOMING_TRACE_ID_HEX}-00f067aa0ba902b7-01"


@contextmanager
def _propagation_only_with_incoming_meta(meta: dict[str, str] | None):
    """Run in propagation_only mode with request_ctx exposing the given meta."""
    original = fastmcp.settings.telemetry_mode
    fastmcp.settings.telemetry_mode = "propagation_only"
    fake_req_ctx = Mock()
    fake_req_ctx.meta = meta
    fake_request_ctx = Mock()
    fake_request_ctx.get = Mock(return_value=fake_req_ctx)
    try:
        with patch(
            "fastmcp.server.telemetry.request_ctx",
            new=fake_request_ctx,
        ):
            yield
    finally:
        fastmcp.settings.telemetry_mode = original


class TestNativeTelemetryEnabled:
    def test_enabled_by_default(self, trace_exporter: InMemorySpanExporter):
        assert native_telemetry_enabled()

    def test_disabled_in_propagation_only_mode(
        self, trace_exporter: InMemorySpanExporter
    ):
        original = fastmcp.settings.telemetry_mode
        try:
            fastmcp.settings.telemetry_mode = "propagation_only"
            assert not native_telemetry_enabled()
        finally:
            fastmcp.settings.telemetry_mode = original

    def test_disabled_inside_suppress_context(
        self, trace_exporter: InMemorySpanExporter
    ):
        assert native_telemetry_enabled()
        with suppress_fastmcp_telemetry():
            assert not native_telemetry_enabled()
        assert native_telemetry_enabled()


class TestSuppressFastMCPTelemetry:
    def test_nests_correctly(self, trace_exporter: InMemorySpanExporter):
        assert native_telemetry_enabled()
        with suppress_fastmcp_telemetry():
            assert not native_telemetry_enabled()
            with suppress_fastmcp_telemetry():
                assert not native_telemetry_enabled()
            # Outer suppression still active after inner exits
            assert not native_telemetry_enabled()
        assert native_telemetry_enabled()

    def test_restores_on_exception(self, trace_exporter: InMemorySpanExporter):
        try:
            with suppress_fastmcp_telemetry():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert native_telemetry_enabled()


class TestGetNoopSpan:
    def test_returns_invalid_span(self, trace_exporter: InMemorySpanExporter):
        assert get_noop_span() is INVALID_SPAN


class TestServerSpanSuppression:
    def test_server_span_emits_no_spans_when_suppressed(
        self, trace_exporter: InMemorySpanExporter
    ):
        from fastmcp.server.telemetry import server_span

        with suppress_fastmcp_telemetry():
            with server_span(
                name="test_op",
                method="tools/call",
                server_name="test-server",
                component_type="tool",
                component_key="tool://test",
            ) as span:
                assert span is INVALID_SPAN

        assert len(trace_exporter.get_finished_spans()) == 0

    def test_server_span_emits_spans_when_not_suppressed(
        self, trace_exporter: InMemorySpanExporter
    ):
        from fastmcp.server.telemetry import server_span

        with server_span(
            name="test_op",
            method="tools/call",
            server_name="test-server",
            component_type="tool",
            component_key="tool://test",
        ):
            pass

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test_op"

    def test_server_span_propagation_only_mode(
        self, trace_exporter: InMemorySpanExporter
    ):
        from fastmcp.server.telemetry import server_span

        original = fastmcp.settings.telemetry_mode
        try:
            fastmcp.settings.telemetry_mode = "propagation_only"
            with server_span(
                name="should_not_appear",
                method="tools/call",
                server_name="test-server",
                component_type="tool",
                component_key="tool://test",
            ) as span:
                assert span is INVALID_SPAN

            assert len(trace_exporter.get_finished_spans()) == 0
        finally:
            fastmcp.settings.telemetry_mode = original


class TestPropagationOnlyInheritsIncomingTrace:
    """The headline guarantee of propagation_only mode: even though FastMCP
    emits no spans of its own, an incoming W3C traceparent in the request
    `_meta` must still parent downstream user-created spans."""

    def test_downstream_span_inherits_incoming_trace_id(
        self, trace_exporter: InMemorySpanExporter
    ):
        from fastmcp.server.telemetry import server_span

        with _propagation_only_with_incoming_meta(
            {"traceparent": _INCOMING_TRACEPARENT}
        ):
            with server_span(
                name="suppressed",
                method="tools/call",
                server_name="test-server",
                component_type="tool",
                component_key="tool://test",
            ) as span:
                assert span is INVALID_SPAN
                # A span the *user* creates inside their handler.
                tracer = otel_trace.get_tracer("user-code")
                with tracer.start_as_current_span("user-span") as child:
                    child_trace_id = child.get_span_context().trace_id

        # FastMCP emitted nothing of its own...
        spans = trace_exporter.get_finished_spans()
        assert [s.name for s in spans] == ["user-span"]
        # ...but the user's span inherited the incoming distributed trace.
        expected = int(_INCOMING_TRACE_ID_HEX, 16)
        assert child_trace_id == expected
        assert spans[0].context.trace_id == expected

    def test_downstream_span_starts_new_trace_without_incoming_context(
        self, trace_exporter: InMemorySpanExporter
    ):
        from fastmcp.server.telemetry import server_span

        with _propagation_only_with_incoming_meta(None):
            with server_span(
                name="suppressed",
                method="tools/call",
                server_name="test-server",
                component_type="tool",
                component_key="tool://test",
            ) as span:
                assert span is INVALID_SPAN
                tracer = otel_trace.get_tracer("user-code")
                with tracer.start_as_current_span("user-span") as child:
                    child_trace_id = child.get_span_context().trace_id

        # No incoming traceparent → a fresh root trace, not the fixed one.
        assert child_trace_id != int(_INCOMING_TRACE_ID_HEX, 16)
        spans = trace_exporter.get_finished_spans()
        assert [s.name for s in spans] == ["user-span"]


class TestDelegateSpanSuppression:
    def test_delegate_span_emits_no_spans_when_suppressed(
        self, trace_exporter: InMemorySpanExporter
    ):
        from fastmcp.server.telemetry import delegate_span

        with suppress_fastmcp_telemetry():
            with delegate_span(
                name="test_delegate",
                provider_type="FastMCPProvider",
                component_key="tool://test",
            ) as span:
                assert span is INVALID_SPAN

        assert len(trace_exporter.get_finished_spans()) == 0


class TestClientSpanSuppression:
    def test_client_span_emits_no_spans_when_suppressed(
        self, trace_exporter: InMemorySpanExporter
    ):
        from fastmcp.client.telemetry import client_span

        with suppress_fastmcp_telemetry():
            with client_span(
                name="test_client",
                method="tools/call",
                component_key="tool://test",
            ) as span:
                assert span is INVALID_SPAN

        assert len(trace_exporter.get_finished_spans()) == 0

    def test_client_span_emits_spans_when_not_suppressed(
        self, trace_exporter: InMemorySpanExporter
    ):
        from fastmcp.client.telemetry import client_span

        with client_span(
            name="test_client",
            method="tools/call",
            component_key="tool://test",
        ):
            pass

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test_client"
