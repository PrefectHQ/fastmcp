"""Tests for the core telemetry module."""

from __future__ import annotations

from opentelemetry.metrics import Meter, NoOpMeter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NonRecordingSpan, Tracer

from fastmcp.server.telemetry import get_auth_span_attributes
from fastmcp.telemetry import INSTRUMENTATION_NAME, get_meter, get_tracer


class TestGetTracer:
    def test_returns_tracer(self):
        tracer = get_tracer()
        assert isinstance(tracer, Tracer)

    def test_returns_tracer_with_version(self):
        tracer = get_tracer("1.0.0")
        assert isinstance(tracer, Tracer)

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

    def test_tracer_noop_without_sdk(self):
        # Without SDK configured, spans are non-recording
        from opentelemetry.trace import ProxyTracer

        tracer = get_tracer()
        # ProxyTracer wraps real or no-op tracer
        assert isinstance(tracer, (Tracer, ProxyTracer))
        span = tracer.start_span("test")
        # Non-recording spans don't capture data
        assert isinstance(span, NonRecordingSpan) or span.is_recording()


class TestGetMeter:
    def test_returns_meter(self):
        meter = get_meter()
        assert isinstance(meter, (Meter, NoOpMeter))

    def test_returns_meter_with_version(self):
        meter = get_meter("1.0.0")
        assert isinstance(meter, (Meter, NoOpMeter))


class TestInstrumentationName:
    def test_instrumentation_name(self):
        assert INSTRUMENTATION_NAME == "fastmcp"


class TestGetAuthSpanAttributes:
    def test_returns_empty_dict_when_no_context(self):
        # No request context available
        attrs = get_auth_span_attributes()
        assert attrs == {}
