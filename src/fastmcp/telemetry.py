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

from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from opentelemetry.trace import get_tracer as otel_get_tracer

INSTRUMENTATION_NAME = "fastmcp"

TRACE_PARENT_KEY = "fastmcp.traceparent"
TRACE_STATE_KEY = "fastmcp.tracestate"


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
        return propagate.extract(carrier)
    return otel_context.get_current()


__all__ = [
    "INSTRUMENTATION_NAME",
    "TRACE_PARENT_KEY",
    "TRACE_STATE_KEY",
    "extract_trace_context",
    "get_tracer",
    "inject_trace_context",
    "record_span_error",
]
