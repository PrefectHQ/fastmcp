"""Tests for the core telemetry module."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fastmcp.server.telemetry import get_auth_span_attributes
from fastmcp.telemetry import (
    INSTRUMENTATION_NAME,
    TRACE_PARENT_KEY,
    extract_trace_context,
    get_tracer,
    inject_trace_context,
)


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


VALID_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
VALID_TRACESTATE = "congo=t61rcWkgMzE"


class TestInjectTraceContext:
    def test_injects_bare_keys(self, trace_exporter: InMemorySpanExporter):
        tracer = get_tracer()
        with tracer.start_as_current_span("test"):
            meta = inject_trace_context()

        assert meta is not None
        assert TRACE_PARENT_KEY in meta
        assert meta[TRACE_PARENT_KEY].startswith("00-")


class TestExtractTraceContext:
    def test_bare_traceparent(self, trace_exporter: InMemorySpanExporter):
        ctx = extract_trace_context({"traceparent": VALID_TRACEPARENT})
        span_ctx = trace.get_current_span(ctx).get_span_context()
        assert span_ctx.is_valid
        assert format(span_ctx.trace_id, "032x") == "0af7651916cd43dd8448eb211c80319c"

    def test_bare_tracestate(self, trace_exporter: InMemorySpanExporter):
        ctx = extract_trace_context(
            {
                "traceparent": VALID_TRACEPARENT,
                "tracestate": VALID_TRACESTATE,
            }
        )
        span_ctx = trace.get_current_span(ctx).get_span_context()
        assert span_ctx.is_valid
        assert span_ctx.trace_state.get("congo") == "t61rcWkgMzE"

    def test_none_meta_returns_current_context(
        self, trace_exporter: InMemorySpanExporter
    ):
        ctx = extract_trace_context(None)
        assert ctx is not None

    def test_empty_meta_returns_current_context(
        self, trace_exporter: InMemorySpanExporter
    ):
        ctx = extract_trace_context({})
        span_ctx = trace.get_current_span(ctx).get_span_context()
        assert not span_ctx.is_valid
