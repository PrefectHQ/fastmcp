from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult
from opentelemetry.trace import Span as APISpan

from fastmcp.client.telemetry import client_span
from fastmcp.server.telemetry import delegate_span, seam_span, server_span


class OnStartRecorder(SpanProcessor):
    def __init__(self) -> None:
        self.attributes: dict[str, dict[str, object]] = {}

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        self.attributes[span.name] = dict(span.attributes or {})


class NonForwardingSampler(Sampler):
    """Samples every span but never forwards the attributes it was handed.

    Mirrors a real-world custom sampler that builds its own `SamplingResult`
    without threading through the `attributes` it received — the
    `attributes` parameter defaults to `None`, so `RECORD_AND_SAMPLE` with no
    `attributes` argument reproduces the regression: OTel's `Tracer.start_span`
    constructs the span from `sampling_result.attributes`, not from the
    `attributes` kwarg passed to `start_as_current_span`.
    """

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: object = None,
        attributes: object = None,
        links: object = None,
        trace_state: object = None,
    ) -> SamplingResult:
        return SamplingResult(Decision.RECORD_AND_SAMPLE)

    def get_description(self) -> str:
        return "NonForwardingSampler"


def test_known_span_attributes_are_available_on_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = OnStartRecorder()
    provider = TracerProvider()
    provider.add_span_processor(recorder)
    tracer = provider.get_tracer("test")
    monkeypatch.setattr("fastmcp.client.telemetry.get_tracer", lambda: tracer)
    monkeypatch.setattr("fastmcp.server.telemetry.get_tracer", lambda: tracer)

    with client_span(
        "client test",
        method="tools/call",
        component_key="tool:echo@",
        tool_name="echo",
    ):
        pass
    with server_span(
        "server test",
        method="tools/call",
        server_name="test-server",
        component_type="tool",
        component_key="tool:echo@",
        tool_name="echo",
    ):
        pass
    with seam_span("initialize test", server_name="test-server"):
        pass
    with delegate_span(
        "delegate test",
        provider_type="LocalProvider",
        component_key="tool:echo@",
        method="tools/call",
    ):
        pass

    assert recorder.attributes["client test"] == {
        "mcp.method.name": "tools/call",
        "fastmcp.component.key": "tool:echo@",
        "gen_ai.tool.name": "echo",
    }
    assert recorder.attributes["server test"] == {
        "mcp.method.name": "tools/call",
        "fastmcp.server.name": "test-server",
        "fastmcp.component.type": "tool",
        "fastmcp.component.key": "tool:echo@",
        "gen_ai.tool.name": "echo",
    }
    assert recorder.attributes["initialize test"] == {
        "fastmcp.span.seam": True,
        "mcp.method.name": "initialize test",
        "fastmcp.server.name": "test-server",
    }
    assert recorder.attributes["delegate delegate test"] == {
        "fastmcp.provider.type": "LocalProvider",
        "fastmcp.component.key": "tool:echo@",
        "mcp.method.name": "tools/call",
    }


SPAN_HELPER_CASES = [
    pytest.param(
        lambda: client_span(
            "client test",
            method="tools/call",
            component_key="tool:echo@",
            tool_name="echo",
        ),
        "client test",
        {
            "mcp.method.name": "tools/call",
            "fastmcp.component.key": "tool:echo@",
            "gen_ai.tool.name": "echo",
        },
        id="client_span",
    ),
    pytest.param(
        lambda: server_span(
            "server test",
            method="tools/call",
            server_name="test-server",
            component_type="tool",
            component_key="tool:echo@",
            tool_name="echo",
        ),
        "server test",
        {
            "mcp.method.name": "tools/call",
            "fastmcp.server.name": "test-server",
            "fastmcp.component.type": "tool",
            "fastmcp.component.key": "tool:echo@",
            "gen_ai.tool.name": "echo",
        },
        id="server_span",
    ),
    pytest.param(
        lambda: seam_span("initialize test", server_name="test-server"),
        "initialize test",
        {
            "fastmcp.span.seam": True,
            "mcp.method.name": "initialize test",
            "fastmcp.server.name": "test-server",
        },
        id="seam_span",
    ),
    pytest.param(
        lambda: delegate_span(
            "delegate test",
            provider_type="LocalProvider",
            component_key="tool:echo@",
            method="tools/call",
        ),
        "delegate delegate test",
        {
            "fastmcp.provider.type": "LocalProvider",
            "fastmcp.component.key": "tool:echo@",
            "mcp.method.name": "tools/call",
        },
        id="delegate_span",
    ),
]


@pytest.mark.parametrize(
    ("span_factory", "span_name", "expected_attrs"), SPAN_HELPER_CASES
)
def test_attributes_survive_a_non_forwarding_sampler(
    monkeypatch: pytest.MonkeyPatch,
    span_factory: Callable[[], AbstractContextManager[APISpan]],
    span_name: str,
    expected_attrs: dict[str, object],
) -> None:
    """Regression: a custom Sampler that samples a span but doesn't forward
    the `attributes` it was handed must not erase FastMCP's telemetry.

    OTel's `Tracer.start_span` builds the finished span from
    `sampling_result.attributes`, not from the `attributes` kwarg passed to
    `start_as_current_span`. Built-in samplers forward what they're given, but
    a custom sampler can legally return `SamplingResult(attributes=None)` and
    silently drop everything FastMCP passed in. The span helpers must reapply
    their attributes after span creation so this can't happen.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=NonForwardingSampler())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr("fastmcp.client.telemetry.get_tracer", lambda: tracer)
    monkeypatch.setattr("fastmcp.server.telemetry.get_tracer", lambda: tracer)

    with span_factory():
        pass

    spans = [s for s in exporter.get_finished_spans() if s.name == span_name]
    assert len(spans) == 1
    assert dict(spans[0].attributes or {}) == expected_attrs
