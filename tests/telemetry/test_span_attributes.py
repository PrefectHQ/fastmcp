from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest
from opentelemetry.context import Context
from opentelemetry.sdk.trace import (
    ReadableSpan,
    Span,
    SpanLimits,
    SpanProcessor,
    TracerProvider,
)
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult
from opentelemetry.trace import Span as APISpan
from opentelemetry.util import types as otel_types

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


class RedactingSampler(Sampler):
    """Forwards the attributes it receives, but replaces `mcp.method.name`.

    Mirrors a real-world sampler that deliberately alters a FastMCP attribute
    (e.g. redacting the method name for privacy) rather than failing to
    forward attributes at all. The restore-missing-attributes helper must
    respect this decision: `mcp.method.name` is present on the span, just not
    with FastMCP's original value, so it must not be overwritten.
    """

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: object = None,
        attributes: otel_types.Attributes = None,
        links: object = None,
        trace_state: object = None,
    ) -> SamplingResult:
        forwarded = dict(attributes or {})
        forwarded["mcp.method.name"] = "REDACTED"
        return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes=forwarded)

    def get_description(self) -> str:
        return "RedactingSampler"


class AttributeAddingSampler(Sampler):
    """Forwards the attributes it receives unchanged and adds its own.

    Mirrors a sampler that annotates spans with sampling-policy metadata.
    Both the sampler's own attribute and FastMCP's attributes must survive.
    """

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: object = None,
        attributes: otel_types.Attributes = None,
        links: object = None,
        trace_state: object = None,
    ) -> SamplingResult:
        forwarded = dict(attributes or {})
        forwarded["sampling.policy"] = "always_on"
        return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes=forwarded)

    def get_description(self) -> str:
        return "AttributeAddingSampler"


class FilteringSampler(Sampler):
    """Discards every attribute it receives and substitutes its own.

    Mirrors a real-world sampler that strips component names or resource
    URIs for privacy or cardinality control by returning a `SamplingResult`
    with only its own attribute, ignoring what it was handed entirely. None
    of FastMCP's attributes may survive on the span, and the restore helper
    must not reintroduce them — that would defeat the filter.
    """

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: object = None,
        attributes: otel_types.Attributes = None,
        links: object = None,
        trace_state: object = None,
    ) -> SamplingResult:
        return SamplingResult(
            Decision.RECORD_AND_SAMPLE, attributes={"sampling.policy": "filtered"}
        )

    def get_description(self) -> str:
        return "FilteringSampler"


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


@pytest.mark.parametrize(
    ("span_factory", "span_name", "expected_attrs"), SPAN_HELPER_CASES
)
def test_redacted_attribute_survives_a_redacting_sampler(
    monkeypatch: pytest.MonkeyPatch,
    span_factory: Callable[[], AbstractContextManager[APISpan]],
    span_name: str,
    expected_attrs: dict[str, object],
) -> None:
    """A sampler that deliberately replaces one of FastMCP's attributes (e.g.
    redacting `mcp.method.name` for privacy) must have that decision survive.

    Restoring must only fill in attributes the sampler dropped, never
    overwrite attributes the sampler kept and intentionally changed.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=RedactingSampler())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr("fastmcp.client.telemetry.get_tracer", lambda: tracer)
    monkeypatch.setattr("fastmcp.server.telemetry.get_tracer", lambda: tracer)

    with span_factory():
        pass

    spans = [s for s in exporter.get_finished_spans() if s.name == span_name]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})

    # The redaction must survive — not be clobbered by a blanket reapply.
    assert attrs["mcp.method.name"] == "REDACTED"

    # Every other FastMCP attribute the sampler forwarded unchanged is
    # untouched, and any it dropped are still restored.
    for key, value in expected_attrs.items():
        if key == "mcp.method.name":
            continue
        assert attrs[key] == value


@pytest.mark.parametrize(
    ("span_factory", "span_name", "expected_attrs"), SPAN_HELPER_CASES
)
def test_sampler_added_attribute_survives_alongside_fastmcp_attributes(
    monkeypatch: pytest.MonkeyPatch,
    span_factory: Callable[[], AbstractContextManager[APISpan]],
    span_name: str,
    expected_attrs: dict[str, object],
) -> None:
    """A sampler that forwards attributes unchanged and adds its own must
    keep both: its own attribute and every FastMCP attribute."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=AttributeAddingSampler())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr("fastmcp.client.telemetry.get_tracer", lambda: tracer)
    monkeypatch.setattr("fastmcp.server.telemetry.get_tracer", lambda: tracer)

    with span_factory():
        pass

    spans = [s for s in exporter.get_finished_spans() if s.name == span_name]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})

    assert attrs["sampling.policy"] == "always_on"
    for key, value in expected_attrs.items():
        assert attrs[key] == value


@pytest.mark.parametrize(
    ("span_factory", "span_name", "expected_attrs"), SPAN_HELPER_CASES
)
def test_filtered_attributes_are_not_restored(
    monkeypatch: pytest.MonkeyPatch,
    span_factory: Callable[[], AbstractContextManager[APISpan]],
    span_name: str,
    expected_attrs: dict[str, object],
) -> None:
    """Regression: a sampler that intentionally supplies only its own
    attributes (e.g. to strip component names or resource URIs for privacy
    or cardinality control) must not have FastMCP's attributes restored.

    A gate keyed off "none of our keys are present" can't tell this apart
    from a bare non-forwarding sampler — both leave none of FastMCP's keys
    on the span — so it would restore everything and defeat the filter. The
    fix keys off the span having no attributes at all: a filtering sampler
    leaves the span non-empty (its own attribute is there), which a bare
    non-forwarding sampler never does.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=FilteringSampler())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr("fastmcp.client.telemetry.get_tracer", lambda: tracer)
    monkeypatch.setattr("fastmcp.server.telemetry.get_tracer", lambda: tracer)

    with span_factory():
        pass

    spans = [s for s in exporter.get_finished_spans() if s.name == span_name]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})

    assert attrs == {"sampling.policy": "filtered"}
    for key in expected_attrs:
        assert key not in attrs


@pytest.mark.parametrize(
    ("span_factory", "span_name", "expected_attrs"), SPAN_HELPER_CASES
)
def test_restore_does_not_churn_sdk_attribute_limit_evictions(
    monkeypatch: pytest.MonkeyPatch,
    span_factory: Callable[[], AbstractContextManager[APISpan]],
    span_name: str,
    expected_attrs: dict[str, object],
) -> None:
    """Regression: under a low `OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT`, restoring
    must not reinsert a key the SDK's bounded attribute map already evicted.

    An evicted key is indistinguishable from a sampler-omitted one from
    inside the restore helper, so a per-key "reinsert what's missing"
    strategy would cycle an evicted key back onto the span, which evicts a
    *different* retained key and inflates `dropped_attributes` beyond what
    the SDK's own eviction already cost. The fix gates the restore on the
    span having no attributes at all (plus `dropped_attributes == 0`), so a
    normal forwarding sampler colliding with a low limit is left exactly as
    the SDK computed it — this test proves that by diffing against a
    baseline with the restore step stubbed out entirely.
    """
    monkeypatch.setenv("OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT", "2")
    # SpanLimits reads the env var at construction time, so build it now
    # (after setting the env var) rather than relying on TracerProvider to
    # pick it up implicitly.
    span_limits = SpanLimits()
    assert span_limits.max_span_attributes == 2

    def run(*, stub_restore: bool) -> ReadableSpan:
        # Each run gets its own MonkeyPatch context so patches from one run
        # (e.g. stubbing the restore step for the baseline) don't leak into
        # the other — both runs must exercise their own code path.
        with pytest.MonkeyPatch.context() as mp:
            exporter = InMemorySpanExporter()
            provider = TracerProvider(span_limits=span_limits)
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            tracer = provider.get_tracer("test")
            mp.setattr("fastmcp.client.telemetry.get_tracer", lambda: tracer)
            mp.setattr("fastmcp.server.telemetry.get_tracer", lambda: tracer)
            if stub_restore:
                mp.setattr(
                    "fastmcp.client.telemetry.restore_dropped_attributes",
                    lambda span, attrs: None,
                )
                mp.setattr(
                    "fastmcp.server.telemetry.restore_dropped_attributes",
                    lambda span, attrs: None,
                )

            with span_factory():
                pass

            spans = [s for s in exporter.get_finished_spans() if s.name == span_name]
            assert len(spans) == 1
            return spans[0]

    baseline = run(stub_restore=True)
    # The whole test is moot if the limit didn't actually bind.
    assert baseline.dropped_attributes > 0

    with_restore = run(stub_restore=False)

    assert dict(with_restore.attributes or {}) == dict(baseline.attributes or {})
    assert with_restore.dropped_attributes == baseline.dropped_attributes
