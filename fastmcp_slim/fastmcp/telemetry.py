"""OpenTelemetry instrumentation for FastMCP.

This module provides native OpenTelemetry integration for FastMCP servers and clients.
It uses only the opentelemetry-api package, so telemetry is a no-op unless the user
installs an OpenTelemetry SDK and configures exporters.

Example usage with SDK:
    ```python
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    # Configure the SDK (user responsibility)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)

    # Now FastMCP will emit traces
    from fastmcp import FastMCP
    mcp = FastMCP("my-server")
    ```
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.trace import (
    INVALID_SPAN,
    NoOpTracer,
    Span,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
)
from opentelemetry.trace import get_tracer as otel_get_tracer
from opentelemetry.util import types as otel_types

if TYPE_CHECKING:
    from fastmcp.settings import TELEMETRY_MODE as TelemetryMode

INSTRUMENTATION_NAME = "fastmcp"

TRACE_PARENT_KEY = "traceparent"
TRACE_STATE_KEY = "tracestate"


class _DisabledTracer(NoOpTracer):
    """A tracer that neither records spans nor touches the OTel context.

    When telemetry is disabled FastMCP must be fully transparent. The stock
    `NoOpTracer.start_as_current_span` still *attaches* a `NonRecordingSpan` to
    the current OTel context, so an enclosing application span (from ASGI/HTTP
    instrumentation or a user-created span) is hidden while a FastMCP span
    helper is active — `trace.get_current_span()` inside a handler would then
    return that non-recording span instead of the caller's span. This tracer
    yields the invalid span *without* entering it as current, leaving the
    surrounding trace context untouched.
    """

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        context: Context | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: otel_types.Attributes = None,
        links: Any = None,
        start_time: int | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
        end_on_exit: bool = True,
    ) -> Iterator[Span]:
        yield INVALID_SPAN


_DISABLED_TRACER = _DisabledTracer()

_SUPPRESS_KEY = otel_context.create_key("fastmcp_suppress_telemetry")


def telemetry_mode() -> "TelemetryMode":
    """Resolve the effective telemetry mode for the current context.

    This is `fastmcp.settings.telemetry_mode`, except that an active
    `suppress_fastmcp_telemetry()` block downgrades `native` to
    `propagation_only`. Suppression never upgrades or overrides `off`: `off`
    means FastMCP touches nothing, and a narrower request to skip FastMCP's
    spans cannot re-enable the context propagation `off` deliberately omits.
    """
    import fastmcp

    mode: TelemetryMode = fastmcp.settings.telemetry_mode
    if mode == "native" and otel_context.get_value(_SUPPRESS_KEY):
        return "propagation_only"
    return mode


def native_spans_enabled() -> bool:
    """Whether FastMCP should create its own spans right now."""
    return telemetry_mode() == "native"


@contextmanager
def suppress_fastmcp_telemetry() -> Iterator[None]:
    """Suppress FastMCP's own spans without disabling trace propagation.

    Scoped equivalent of `telemetry_mode="propagation_only"`, for callers that
    embed FastMCP inside their own instrumented stack and want to own the MCP
    span hierarchy for a specific block. Narrower than OpenTelemetry's global
    instrumentation suppression: only FastMCP's spans are skipped, so nested
    instrumentation (HTTP clients, databases) keeps emitting, and trace context
    still flows through `_meta` so those spans are parented correctly.

    Has no effect when `telemetry_mode` is already `off`.
    """
    token = otel_context.attach(otel_context.set_value(_SUPPRESS_KEY, True))
    try:
        yield
    finally:
        otel_context.detach(token)


def get_tracer(version: str | None = None) -> Tracer:
    """Get the FastMCP tracer for creating spans.

    Instrumentation is on by default. FastMCP uses only the OpenTelemetry API,
    so span creation is a no-op with negligible overhead unless an OpenTelemetry
    SDK and exporter are configured. When `fastmcp.settings.telemetry_mode` is
    `propagation_only` or `off` — or the caller is inside a
    `suppress_fastmcp_telemetry()` block — this returns a pass-through tracer
    that creates no spans and leaves the current OTel context untouched even
    when an SDK is configured.

    Args:
        version: Optional version string for the instrumentation

    Returns:
        A tracer instance. Returns a non-attaching pass-through tracer when
        FastMCP's own spans are disabled; span creation is otherwise a no-op
        unless an SDK is configured.
    """
    if not native_spans_enabled():
        return _DISABLED_TRACER
    return otel_get_tracer(INSTRUMENTATION_NAME, version)


def inject_trace_context(
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Inject current trace context into a meta dict for MCP request propagation.

    Args:
        meta: Optional existing meta dict to merge with trace context

    Returns:
        A new dict containing the original meta (if any) plus trace context keys,
        or None if no trace context to inject and meta was None
    """
    # `off` means FastMCP touches nothing, outbound propagation included.
    # `propagation_only` still injects — carrying context is the whole point.
    if telemetry_mode() == "off":
        return meta

    carrier: dict[str, str] = {}
    propagate.inject(carrier)

    trace_meta: dict[str, Any] = {}
    if "traceparent" in carrier:
        trace_meta[TRACE_PARENT_KEY] = carrier["traceparent"]
    if "tracestate" in carrier:
        trace_meta[TRACE_STATE_KEY] = carrier["tracestate"]

    if trace_meta:
        return {**(meta or {}), **trace_meta}
    return meta


def record_span_error(span: Span, exception: BaseException) -> None:
    """Record an exception on a span and set error status."""
    span.record_exception(exception)
    span.set_status(Status(StatusCode.ERROR))


@runtime_checkable
class _AttributeReadableSpan(Protocol):
    """Structural type for spans that expose their current attribute state.

    The `opentelemetry-api` `Span` ABC has no way to read attributes back —
    only SDK span implementations (e.g. `opentelemetry.sdk.trace.ReadableSpan`)
    expose `.attributes` and `.dropped_attributes`. FastMCP only depends on
    `opentelemetry-api`, so this module can't import the SDK class to
    `isinstance`-check against it. A runtime-checkable `Protocol` gets the
    same structural narrowing without that import: spans that don't expose
    this state (e.g. `NonRecordingSpan`) simply fail the check.
    """

    @property
    def attributes(self) -> Mapping[str, otel_types.AttributeValue] | None: ...

    @property
    def dropped_attributes(self) -> int: ...


def restore_dropped_attributes(
    span: Span, attrs: Mapping[str, otel_types.AttributeValue]
) -> None:
    """Restore FastMCP attributes a non-forwarding sampler dropped entirely.

    `Tracer.start_span` builds the span from `SamplingResult.attributes`, not
    the `attributes=` kwarg it was given for creation — a custom `Sampler`
    whose `SamplingResult.attributes` defaults to `None` silently discards
    every attribute FastMCP passed at creation time. Call this immediately
    after span creation to recover from that case.

    The restore only fires when the span has *no* attributes at all AND the
    SDK hasn't evicted anything (`dropped_attributes == 0`):

    - A bare, non-forwarding sampler (the regression this exists to fix)
      leaves the span with an empty attribute mapping, so everything is
      restored.
    - A sampler that supplied any attributes of its own — whether by
      forwarding ours untouched, redacting or replacing some of our values,
      or substituting its own attributes entirely (e.g. to strip component
      names or resource URIs for privacy or cardinality control) — leaves
      the span non-empty, so it is left alone entirely. This is what makes
      the gate precise: a sampler that deliberately supplies only its own
      attributes must not have them clobbered by a restore that assumes
      "no FastMCP keys" means "sampler forwarding failed."
    - A sampler that forwards most of our attributes but deliberately drops
      one is still non-empty, so it's covered by the same "leave alone"
      branch — a dropped key here is indistinguishable from the SDK's
      bounded attribute map evicting it, and reinserting it would just push
      the map's bound and evict a *different* retained key, churning which
      attributes survive without changing how many are lost. No attempt is
      made to restore individual missing keys; the gate is all-or-nothing.
    - A low `OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT` that evicts every attribute a
      forwarding sampler passed through is indistinguishable, from the
      span's attribute state alone, from a bare non-forwarding sampler —
      both leave an empty mapping. `dropped_attributes == 0` is what tells
      them apart: eviction always increments it, so that case is correctly
      excluded from the restore and the SDK's bounded map is left as
      computed.

    Callers are expected to guard this with `if span.is_recording():`; it
    does no work worth skipping for non-recording spans, but the check is
    kept at call sites so it reads alongside the sibling `is_recording()`
    guards already in those functions.
    """
    existing: Mapping[str, otel_types.AttributeValue] = {}
    dropped = 0
    if isinstance(span, _AttributeReadableSpan):
        existing = span.attributes or {}
        dropped = span.dropped_attributes
    if not existing and dropped == 0:
        span.set_attributes(attrs)


def extract_trace_context(meta: dict[str, Any] | None) -> Context:
    """Extract trace context from an MCP request meta dict.

    If already in a valid trace (e.g., from HTTP propagation), the existing
    trace context is preserved and meta is not used.

    Args:
        meta: The meta dict from an MCP request (ctx.request_context.meta)

    Returns:
        An OpenTelemetry Context with the extracted trace context,
        or the current context if no trace context found or already in a trace
    """
    # `off` means FastMCP touches nothing, including the surrounding context.
    if telemetry_mode() == "off":
        return otel_context.get_current()

    # Don't override existing trace context (e.g., from HTTP propagation)
    current_span = trace.get_current_span()
    if current_span.get_span_context().is_valid:
        return otel_context.get_current()

    if not meta:
        return otel_context.get_current()

    carrier: dict[str, str] = {}
    if TRACE_PARENT_KEY in meta:
        carrier["traceparent"] = str(meta[TRACE_PARENT_KEY])
    if TRACE_STATE_KEY in meta:
        carrier["tracestate"] = str(meta[TRACE_STATE_KEY])

    if carrier:
        # Extract *onto the current context* rather than a fresh root, so the
        # incoming parent is added without discarding context values the
        # caller already established — active baggage, and FastMCP's own
        # suppression marker, which would otherwise be dropped the moment the
        # extracted context is attached.
        return propagate.extract(carrier, context=otel_context.get_current())
    return otel_context.get_current()


__all__ = [
    "INSTRUMENTATION_NAME",
    "TRACE_PARENT_KEY",
    "TRACE_STATE_KEY",
    "extract_trace_context",
    "get_tracer",
    "inject_trace_context",
    "native_spans_enabled",
    "record_span_error",
    "restore_dropped_attributes",
    "suppress_fastmcp_telemetry",
    "telemetry_mode",
]
