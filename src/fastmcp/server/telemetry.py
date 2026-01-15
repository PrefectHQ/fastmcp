"""Server-side telemetry helpers."""

from collections.abc import Generator
from contextlib import contextmanager

from mcp.server.lowlevel.server import request_ctx
from opentelemetry.context import Context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from fastmcp.telemetry import extract_trace_context, get_tracer


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


def _get_parent_trace_context() -> Context | None:
    """Get parent trace context from request meta for distributed tracing."""
    try:
        req_ctx = request_ctx.get()
        if req_ctx and hasattr(req_ctx, "meta") and req_ctx.meta:
            return extract_trace_context(dict(req_ctx.meta))
    except LookupError:
        pass
    return None


@contextmanager
def server_span(
    name: str,
    method: str,
    server_name: str,
    component_type: str,
    component_key: str,
    resource_uri: str | None = None,
) -> Generator[Span, None, None]:
    """Create a SERVER span with standard MCP attributes and auth context.

    Automatically records any exception on the span and sets error status.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(
        name,
        context=_get_parent_trace_context(),
        kind=SpanKind.SERVER,
    ) as span:
        attrs: dict[str, str] = {
            # RPC semantic conventions
            "rpc.system": "mcp",
            "rpc.service": server_name,
            "rpc.method": method,
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
        span.set_attributes(attrs)
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR))
            raise


@contextmanager
def delegate_span(
    name: str,
    provider_type: str,
    component_key: str,
) -> Generator[Span, None, None]:
    """Create an INTERNAL span for provider delegation.

    Used by FastMCPProvider when delegating to mounted servers.
    Automatically records any exception on the span and sets error status.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(f"delegate {name}") as span:
        span.set_attributes(
            {
                "fastmcp.provider.type": provider_type,
                "fastmcp.component.key": component_key,
            }
        )
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR))
            raise


__all__ = [
    "delegate_span",
    "get_auth_span_attributes",
    "get_session_span_attributes",
    "server_span",
]
