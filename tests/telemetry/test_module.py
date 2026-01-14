"""Tests for the core telemetry module."""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fastmcp.server.telemetry import get_auth_span_attributes
from fastmcp.telemetry import INSTRUMENTATION_NAME, get_tracer


class TestGetTracer:
    def test_tracer_uses_instrumentation_name(
        self, trace_exporter: InMemorySpanExporter
    ):
        tracer = get_tracer()
        with tracer.start_as_current_span("test-span"):
            pass

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1
        scope = spans[0].instrumentation_scope
        assert scope is not None
        assert scope.name == INSTRUMENTATION_NAME


class TestGetAuthSpanAttributes:
    def test_returns_empty_dict_when_no_context(self):
        attrs = get_auth_span_attributes()
        assert attrs == {}
