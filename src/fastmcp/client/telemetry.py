"""Client-side telemetry helpers."""

from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from fastmcp.exceptions import ToolError as _ToolError
from fastmcp.telemetry import _NOOP_SPAN, get_tracer, is_telemetry_opted_out


@contextmanager
def client_span(
    name: str,
    method: str,
    component_key: str,
    session_id: str | None = None,
    resource_uri: str | None = None,
    tool_name: str | None = None,
    prompt_name: str | None = None,
) -> Generator[Span, None, None]:
    """Create a CLIENT span with standard MCP attributes.

    Automatically records any exception on the span and sets error status.

    When ``FASTMCP_TELEMETRY_OPT_OUT`` is enabled, yields a
    non-recording no-op span without creating a real span or
    modifying the active OpenTelemetry context.
    """
    if is_telemetry_opted_out():
        yield _NOOP_SPAN
        return

    tracer = get_tracer()
    with tracer.start_as_current_span(name, kind=SpanKind.CLIENT) as span:
        if span.is_recording():
            attrs: dict[str, str] = {
                # MCP semantic conventions
                "mcp.method.name": method,
                # FastMCP-specific attributes
                "fastmcp.component.key": component_key,
            }
            if session_id is not None:
                attrs["mcp.session.id"] = session_id
            if resource_uri:
                attrs["mcp.resource.uri"] = resource_uri
            if tool_name is not None:
                attrs["gen_ai.tool.name"] = tool_name
            if prompt_name is not None:
                attrs["gen_ai.prompt.name"] = prompt_name
            span.set_attributes(attrs)
        try:
            yield span
        except Exception as e:
            if span.is_recording():
                error_type = (
                    "tool_error" if isinstance(e, _ToolError) else type(e).__qualname__
                )
                span.set_attribute("error.type", error_type)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
            raise


__all__ = ["client_span"]
