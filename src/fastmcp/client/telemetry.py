"""Client-side telemetry helpers."""

from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from fastmcp.telemetry import get_tracer


@contextmanager
def client_span(
    name: str,
    method: str,
    component_key: str,
    session_id: str | None = None,
) -> Generator[Span, None, None]:
    """Create a CLIENT span with standard MCP attributes.

    Automatically records any exception on the span and sets error status.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(name, kind=SpanKind.CLIENT) as span:
        attrs: dict[str, str] = {
            "rpc.system": "mcp",
            "rpc.method": method,
            "fastmcp.component.key": component_key,
        }
        if session_id:
            attrs["fastmcp.session.id"] = session_id
        span.set_attributes(attrs)
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR))
            raise


__all__ = ["client_span"]
