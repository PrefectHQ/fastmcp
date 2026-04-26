"""Server-side telemetry helpers."""

from collections.abc import Generator
from contextlib import contextmanager

from mcp.server.lowlevel.server import request_ctx
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import (
    Link,
    Span,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
)

from fastmcp.exceptions import ToolError as _ToolError
from fastmcp.server.http import AMBIENT_SPAN_CONTEXT_SCOPE_KEY
from fastmcp.telemetry import (
    extract_propagation_keys_from_meta,
    extract_trace_context,
    get_noop_span,
    get_tracer,
    native_telemetry_enabled,
)


def get_auth_span_attributes() -> dict[str, str]:
    """Get auth attributes for the current request, if authenticated."""
    from fastmcp.server.dependencies import get_access_token

    attrs: dict[str, str] = {}
    try:
        token = get_access_token()
        if token:
            if token.client_id:
                attrs["enduser.id"] = token.client_id
            if token.scopes:
                attrs["enduser.scope"] = " ".join(token.scopes)
    except RuntimeError:
        pass
    return attrs


def get_session_span_attributes() -> dict[str, str]:
    """Get session attributes for the current request."""
    from fastmcp.server.dependencies import get_context

    attrs: dict[str, str] = {}
    try:
        ctx = get_context()
        if ctx.request_context is not None and ctx.session_id is not None:
            attrs["mcp.session.id"] = ctx.session_id
    except RuntimeError:
        pass
    return attrs


def _get_parent_trace_context() -> tuple[Context | None, list[Link] | None]:
    """Resolve MCP server parent context plus any ambient transport links."""
    ambient_span_context = _get_ambient_or_current_span_context()

    try:
        req_ctx = request_ctx.get()
        if req_ctx and hasattr(req_ctx, "meta") and req_ctx.meta:
            meta = dict(req_ctx.meta)
            if extract_propagation_keys_from_meta(meta):
                parent_context = extract_trace_context(meta)
                parent_span_context = trace.get_current_span(
                    parent_context
                ).get_span_context()
                if (
                    ambient_span_context.is_valid
                    and parent_span_context != ambient_span_context
                ):
                    return parent_context, [Link(ambient_span_context)]
                return parent_context, None
    except LookupError:
        pass

    if ambient_span_context.is_valid:
        return otel_context.get_current(), None

    return None, None


def _get_ambient_or_current_span_context() -> SpanContext:
    """Resolve the ambient transport span, falling back to the current span.

    Returns the span context stored in the request scope by an outer transport
    middleware, if present and valid. Otherwise returns the current active span.
    """
    try:
        req_ctx = request_ctx.get()
    except LookupError:
        req_ctx = None

    if req_ctx is not None:
        request = getattr(req_ctx, "request", None)
        if request is not None:
            ambient_span_context = request.scope.get(AMBIENT_SPAN_CONTEXT_SCOPE_KEY)
            if (
                isinstance(ambient_span_context, SpanContext)
                and ambient_span_context.is_valid
            ):
                return ambient_span_context

    return trace.get_current_span().get_span_context()


@contextmanager
def server_span(
    name: str,
    method: str,
    server_name: str,
    component_type: str,
    component_key: str,
    resource_uri: str | None = None,
    tool_name: str | None = None,
    prompt_name: str | None = None,
) -> Generator[Span, None, None]:
    """Create a SERVER span with standard MCP attributes and auth context.

    Automatically records any exception on the span and sets error status.
    """
    if not native_telemetry_enabled():
        yield get_noop_span()
        return

    parent_context, links = _get_parent_trace_context()
    tracer = get_tracer()
    span = tracer.start_span(
        name,
        context=parent_context,
        kind=SpanKind.SERVER,
        links=links,
    )
    current_context = trace.set_span_in_context(
        span,
        parent_context if parent_context is not None else otel_context.get_current(),
    )
    token = otel_context.attach(current_context)
    try:
        if span.is_recording():
            attrs: dict[str, str] = {
                # MCP semantic conventions
                "mcp.method.name": method,
                # FastMCP-specific attributes
                "fastmcp.server.name": server_name,
                "fastmcp.component.type": component_type,
                "fastmcp.component.key": component_key,
                **get_auth_span_attributes(),
                **get_session_span_attributes(),
            }
            if resource_uri is not None:
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
    finally:
        otel_context.detach(token)
        span.end()


@contextmanager
def delegate_span(
    name: str,
    provider_type: str,
    component_key: str,
    method: str | None = None,
) -> Generator[Span, None, None]:
    """Create an INTERNAL span for provider delegation.

    Used by FastMCPProvider when delegating to mounted servers.
    Automatically records any exception on the span and sets error status.
    """
    if not native_telemetry_enabled():
        yield get_noop_span()
        return

    tracer = get_tracer()
    with tracer.start_as_current_span(f"delegate {name}") as span:
        if span.is_recording():
            attrs: dict[str, str] = {
                "fastmcp.provider.type": provider_type,
                "fastmcp.component.key": component_key,
            }
            if method is not None:
                attrs["mcp.method.name"] = method
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


__all__ = [
    "delegate_span",
    "get_auth_span_attributes",
    "get_session_span_attributes",
    "server_span",
]
