"""Tests for telemetry interoperability modes.

Validates that FastMCP's own spans can be suppressed — globally via
`telemetry_mode` or per-block via `suppress_fastmcp_telemetry()` — while trace
context propagation keeps working in `propagation_only` mode and is fully
disabled in `off` mode.
"""

from __future__ import annotations

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import INVALID_SPAN, SpanKind

import fastmcp
from fastmcp import Client, Context, FastMCP
from fastmcp.client.telemetry import client_span
from fastmcp.server.telemetry import delegate_span, server_span
from fastmcp.telemetry import (
    extract_trace_context,
    inject_trace_context,
    native_spans_enabled,
    suppress_fastmcp_telemetry,
    telemetry_mode,
)

# A well-formed W3C traceparent for extraction tests.
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


@pytest.fixture
def mode(monkeypatch: pytest.MonkeyPatch):
    """Set `fastmcp.settings.telemetry_mode` for the duration of a test."""

    def _set(value: str) -> None:
        monkeypatch.setattr(fastmcp.settings, "telemetry_mode", value)

    return _set


def fastmcp_spans(exporter: InMemorySpanExporter) -> list[str]:
    """Names of spans emitted by FastMCP's own instrumentation scope."""
    return [
        s.name
        for s in exporter.get_finished_spans()
        if s.instrumentation_scope is not None
        and s.instrumentation_scope.name == "fastmcp"
    ]


class TestTelemetryModeResolution:
    def test_native_by_default(self):
        assert telemetry_mode() == "native"
        assert native_spans_enabled()

    @pytest.mark.parametrize("value", ["propagation_only", "off"])
    def test_setting_disables_native_spans(self, value: str, mode):
        mode(value)
        assert telemetry_mode() == value
        assert not native_spans_enabled()

    def test_suppress_downgrades_native_to_propagation_only(self):
        with suppress_fastmcp_telemetry():
            assert telemetry_mode() == "propagation_only"
            assert not native_spans_enabled()
        assert telemetry_mode() == "native"

    def test_suppress_cannot_override_off(self, mode):
        """`off` means FastMCP touches nothing. A narrower request to skip
        FastMCP's spans must not re-enable the propagation `off` omits."""
        mode("off")
        with suppress_fastmcp_telemetry():
            assert telemetry_mode() == "off"

    def test_suppress_nests(self):
        with suppress_fastmcp_telemetry():
            with suppress_fastmcp_telemetry():
                assert not native_spans_enabled()
            # Outer suppression still active after the inner block exits.
            assert not native_spans_enabled()
        assert native_spans_enabled()

    def test_suppress_restores_on_exception(self):
        with pytest.raises(RuntimeError):
            with suppress_fastmcp_telemetry():
                raise RuntimeError("boom")
        assert native_spans_enabled()


class TestSpanHelperSuppression:
    """Every FastMCP span helper goes quiet when its own spans are disabled."""

    @pytest.fixture
    def helpers(self):
        return {
            "server": lambda: server_span(
                name="test_op",
                method="tools/call",
                server_name="test-server",
                component_type="tool",
                component_key="tool://test",
            ),
            "client": lambda: client_span(
                name="test_client",
                method="tools/call",
                component_key="tool://test",
            ),
            "delegate": lambda: delegate_span(
                name="test_delegate",
                provider_type="FastMCPProvider",
                component_key="tool://test",
            ),
        }

    @pytest.mark.parametrize("helper", ["server", "client", "delegate"])
    @pytest.mark.parametrize("value", ["propagation_only", "off"])
    def test_helper_emits_nothing(
        self,
        helper: str,
        value: str,
        helpers,
        mode,
        trace_exporter: InMemorySpanExporter,
    ):
        mode(value)
        with helpers[helper]() as span:
            assert span is INVALID_SPAN
        assert trace_exporter.get_finished_spans() == ()

    @pytest.mark.parametrize("helper", ["server", "client", "delegate"])
    def test_helper_emits_nothing_under_suppress(
        self, helper: str, helpers, trace_exporter: InMemorySpanExporter
    ):
        with suppress_fastmcp_telemetry():
            with helpers[helper]() as span:
                assert span is INVALID_SPAN
        assert trace_exporter.get_finished_spans() == ()

    @pytest.mark.parametrize("helper", ["server", "client", "delegate"])
    def test_helper_emits_by_default(
        self, helper: str, helpers, trace_exporter: InMemorySpanExporter
    ):
        with helpers[helper]():
            pass
        assert len(trace_exporter.get_finished_spans()) == 1


class TestContextPropagation:
    """`propagation_only` keeps trace context flowing; `off` does not."""

    def test_extract_preserves_current_context_values(self):
        """Regression: extracting the incoming traceparent must not discard
        context values the caller already established. Extracting onto a fresh
        root would drop FastMCP's own suppression marker (and any baggage), so
        attaching the result would silently re-enable FastMCP's spans.
        """
        with suppress_fastmcp_telemetry():
            parent = extract_trace_context({"traceparent": TRACEPARENT})
            token = otel_context.attach(parent)
            try:
                assert telemetry_mode() == "propagation_only"
            finally:
                otel_context.detach(token)

    def test_extract_applies_incoming_parent(self, mode):
        mode("propagation_only")
        parent = extract_trace_context({"traceparent": TRACEPARENT})
        token = otel_context.attach(parent)
        try:
            span_context = otel_trace.get_current_span().get_span_context()
            assert format(span_context.trace_id, "032x") == (
                "4bf92f3577b34da6a3ce929d0e0e4736"
            )
        finally:
            otel_context.detach(token)

    def test_off_ignores_incoming_parent(self, mode):
        """`off` is a full pass-through: the incoming context is not applied."""
        mode("off")
        parent = extract_trace_context({"traceparent": TRACEPARENT})
        assert parent is otel_context.get_current()

    def test_off_does_not_inject(self, mode, trace_exporter: InMemorySpanExporter):
        mode("off")
        with otel_trace.get_tracer("test").start_as_current_span("root"):
            assert inject_trace_context({"existing": 1}) == {"existing": 1}

    def test_propagation_only_still_injects(
        self, mode, trace_exporter: InMemorySpanExporter
    ):
        mode("propagation_only")
        with otel_trace.get_tracer("test").start_as_current_span("root"):
            meta = inject_trace_context()
        assert meta is not None and "traceparent" in meta


class TestEndToEnd:
    """A real in-process client drives a real server — nothing monkeypatched
    beyond the setting itself."""

    async def test_propagation_only_parents_downstream_user_spans(
        self, mode, trace_exporter: InMemorySpanExporter
    ):
        mode("propagation_only")
        captured: dict[str, int] = {}

        server = FastMCP("interop-server")

        @server.tool
        async def work(ctx: Context) -> str:
            # A span the *user* creates inside their handler.
            tracer = otel_trace.get_tracer("user-code")
            with tracer.start_as_current_span("user-span") as span:
                captured["downstream"] = span.get_span_context().trace_id
            return "done"

        async with Client(server) as client:
            tracer = otel_trace.get_tracer("client-code")
            with tracer.start_as_current_span("client-root") as root:
                captured["client"] = root.get_span_context().trace_id
                await client.call_tool("work", {})

        names = [s.name for s in trace_exporter.get_finished_spans()]
        assert "user-span" in names and "client-root" in names
        # FastMCP emitted none of its own spans — including the per-request
        # SERVER span opened at the middleware seam, which is the whole point.
        assert fastmcp_spans(trace_exporter) == []
        assert [
            s for s in trace_exporter.get_finished_spans() if s.kind == SpanKind.SERVER
        ] == []
        # ...yet the user's span inherited the incoming distributed trace.
        assert captured["client"] == captured["downstream"]

    async def test_propagation_only_without_incoming_trace(
        self, mode, trace_exporter: InMemorySpanExporter
    ):
        """With no surrounding client span there is no incoming trace. The call
        must still succeed and emit no FastMCP spans."""
        mode("propagation_only")
        captured: dict[str, int] = {}

        server = FastMCP("interop-server")

        @server.tool
        async def work(ctx: Context) -> str:
            tracer = otel_trace.get_tracer("user-code")
            with tracer.start_as_current_span("user-span") as span:
                captured["downstream"] = span.get_span_context().trace_id
            return "done"

        async with Client(server) as client:
            result = await client.call_tool("work", {})

        assert result.data == "done"
        assert fastmcp_spans(trace_exporter) == []
        # A self-rooted trace was created (no incoming parent to inherit).
        assert "downstream" in captured

    async def test_suppress_block_silences_a_single_call(
        self, trace_exporter: InMemorySpanExporter
    ):
        """The scoped form suppresses one call and leaves the next instrumented."""
        server = FastMCP("interop-server")

        @server.tool
        async def work() -> str:
            return "done"

        async with Client(server) as client:
            # Drop the spans the connection handshake already emitted.
            trace_exporter.clear()

            with suppress_fastmcp_telemetry():
                await client.call_tool("work", {})
            assert fastmcp_spans(trace_exporter) == []

            await client.call_tool("work", {})
            assert fastmcp_spans(trace_exporter) != []
