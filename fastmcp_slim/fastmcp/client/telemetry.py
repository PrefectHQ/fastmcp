"""Client-side telemetry helpers."""

from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from fastmcp.exceptions import ToolError as _ToolError
from fastmcp.telemetry import get_tracer, restore_dropped_attributes


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
    """
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

    tracer = get_tracer()
    with tracer.start_as_current_span(
        name, kind=SpanKind.CLIENT, attributes=attrs
    ) as span:
        # Restore: `attributes=attrs` above lets on_start hooks and the
        # sampler see these values at creation time. But OTel's
        # Tracer.start_span builds the span from
        # `sampling_result.attributes`, not the `attributes` kwarg directly —
        # a custom Sampler whose SamplingResult.attributes defaults to None
        # silently drops everything we passed. This only fires when the span
        # ends up with no attributes at all, so any sampler that supplied
        # attributes of its own — forwarding ours, redacting or replacing
        # some, or substituting entirely its own — is left untouched, as is
        # an SDK attribute limit that evicted some.
        if span.is_recording():
            restore_dropped_attributes(span, attrs)
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
