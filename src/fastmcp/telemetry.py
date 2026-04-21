"""OpenTelemetry instrumentation for FastMCP.

This module provides native OpenTelemetry integration for FastMCP servers and
clients. It uses only the opentelemetry-api package, so telemetry is a no-op
unless the user installs an OpenTelemetry SDK and configures exporters.

FastMCP always propagates OpenTelemetry context through MCP ``params._meta``.
Native FastMCP spans can be suppressed globally via
``FASTMCP_TELEMETRY_MODE=propagation_only`` or programmatically with
``suppress_fastmcp_telemetry()`` when another instrumentation layer owns the
MCP span hierarchy.

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

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import propagate
from opentelemetry.context import Context
from opentelemetry.trace import INVALID_SPAN, Span, Status, StatusCode, Tracer
from opentelemetry.trace import get_tracer as otel_get_tracer

INSTRUMENTATION_NAME = "fastmcp"

TRACE_PARENT_KEY = "traceparent"
TRACE_STATE_KEY = "tracestate"
BAGGAGE_KEY = "baggage"

_SUPPRESS_FASTMCP_TELEMETRY_KEY = otel_context.create_key("fastmcp_suppress_telemetry")


def _get_fastmcp_telemetry_mode() -> str:
    """Read the current FastMCP telemetry mode from settings."""
    import fastmcp

    return fastmcp.settings.telemetry_mode


def native_telemetry_enabled() -> bool:
    """Return whether FastMCP should create native MCP spans."""
    return _get_fastmcp_telemetry_mode() == "native" and not otel_context.get_value(
        _SUPPRESS_FASTMCP_TELEMETRY_KEY
    )


@contextmanager
def suppress_fastmcp_telemetry() -> Generator[None, None, None]:
    """Suppress native FastMCP spans while preserving context propagation.

    This is narrower than OpenTelemetry's global instrumentation suppression:
    it disables only FastMCP's own spans, allowing unrelated nested
    instrumentations (HTTP clients, databases, etc.) to continue emitting.
    """
    token = otel_context.attach(
        otel_context.set_value(_SUPPRESS_FASTMCP_TELEMETRY_KEY, True)
    )
    try:
        yield
    finally:
        otel_context.detach(token)


def get_trace_context_carrier(meta: dict[str, Any] | None) -> dict[str, str]:
    """Extract trace-related propagation keys from an MCP ``_meta`` dict."""
    if not meta:
        return {}

    carrier: dict[str, str] = {}
    if TRACE_PARENT_KEY in meta:
        carrier[TRACE_PARENT_KEY] = str(meta[TRACE_PARENT_KEY])
    if TRACE_STATE_KEY in meta:
        carrier[TRACE_STATE_KEY] = str(meta[TRACE_STATE_KEY])
    if BAGGAGE_KEY in meta:
        carrier[BAGGAGE_KEY] = str(meta[BAGGAGE_KEY])
    return carrier


def get_tracer(version: str | None = None) -> Tracer:
    """Get the FastMCP tracer for creating spans.

    Args:
        version: Optional version string for the instrumentation

    Returns:
        A tracer instance. Returns a no-op tracer if no SDK is configured.
    """
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
    carrier: dict[str, str] = {}
    propagate.inject(carrier)

    trace_meta: dict[str, Any] = {}
    if TRACE_PARENT_KEY in carrier:
        trace_meta[TRACE_PARENT_KEY] = carrier[TRACE_PARENT_KEY]
    if TRACE_STATE_KEY in carrier:
        trace_meta[TRACE_STATE_KEY] = carrier[TRACE_STATE_KEY]
    if BAGGAGE_KEY in carrier:
        trace_meta[BAGGAGE_KEY] = carrier[BAGGAGE_KEY]

    if trace_meta:
        return {**(meta or {}), **trace_meta}
    return meta


def record_span_error(span: Span, exception: BaseException) -> None:
    """Record an exception on a span and set error status."""
    span.record_exception(exception)
    span.set_status(Status(StatusCode.ERROR))


def extract_trace_context(meta: dict[str, Any] | None) -> Context:
    """Extract trace context from an MCP request meta dict.

    Args:
        meta: The meta dict from an MCP request (ctx.request_context.meta)

    Returns:
        An OpenTelemetry Context with the extracted trace context,
        or the current context if no trace context was propagated
    """
    carrier = get_trace_context_carrier(meta)
    if carrier:
        return propagate.extract(carrier)
    return otel_context.get_current()


def get_noop_span() -> Span:
    """Return the no-op span used when native FastMCP telemetry is suppressed."""
    return INVALID_SPAN


__all__ = [
    "BAGGAGE_KEY",
    "INSTRUMENTATION_NAME",
    "TRACE_PARENT_KEY",
    "TRACE_STATE_KEY",
    "extract_trace_context",
    "get_noop_span",
    "get_trace_context_carrier",
    "get_tracer",
    "inject_trace_context",
    "native_telemetry_enabled",
    "record_span_error",
    "suppress_fastmcp_telemetry",
]
