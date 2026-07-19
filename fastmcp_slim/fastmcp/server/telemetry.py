"""Server-side telemetry helpers."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry.context import Context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, get_current_span

from fastmcp.exceptions import ToolError as _ToolError
from fastmcp.telemetry import (
    extract_trace_context,
    get_tracer,
    restore_dropped_attributes,
)

# Marker attribute set on the SERVER span opened at the FastMCP middleware seam
# (see `fastmcp.server.low_level.FastMCPServerMiddleware._seam_span`). The seam
# opens one span per inbound request so failures rejected *before* the
# high-level path (auth, not-found, middleware vetoes) are still traced. The
# attribute is set for observability; enrichment detection uses the ContextVar
# below (the API `Span` type does not expose readable attributes).
SEAM_SPAN_MARKER = "fastmcp.span.seam"

# Tracks the SERVER span opened at the current request's seam. When the
# high-level path reaches `server_span` and this span is still the active span,
# `server_span` enriches it with component attributes instead of opening a
# second span — restoring attribute parity on the single per-request span.
_active_seam_span: ContextVar[Span | None] = ContextVar(
    "fastmcp_active_seam_span", default=None
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


def get_protocol_span_attributes() -> dict[str, str]:
    """Get the negotiated MCP protocol version for the current request.

    Mirrors the `mcp.protocol.version` attribute the SDK's own
    `OpenTelemetryMiddleware` sets — FastMCP drops that middleware to avoid a
    duplicate SERVER span, so this restores the attribute on FastMCP's span.
    """
    from fastmcp.server.dependencies import fastmcp_request_ctx

    req_ctx = fastmcp_request_ctx.get()
    if req_ctx is not None and req_ctx.protocol_version:
        return {"mcp.protocol.version": req_ctx.protocol_version}
    return {}


def _get_parent_trace_context() -> Context | None:
    """Get parent trace context from request meta for distributed tracing."""
    from fastmcp.server.dependencies import fastmcp_request_ctx

    req_ctx = fastmcp_request_ctx.get()
    if req_ctx is not None and req_ctx.meta:
        return extract_trace_context(req_ctx.meta)
    return None


def _build_server_span_attrs(
    method: str,
    server_name: str,
    component_type: str,
    component_key: str,
    resource_uri: str | None,
    tool_name: str | None,
    prompt_name: str | None,
) -> dict[str, str]:
    attrs: dict[str, str] = {
        # MCP semantic conventions
        "mcp.method.name": method,
        # FastMCP-specific attributes
        "fastmcp.server.name": server_name,
        "fastmcp.component.type": component_type,
        "fastmcp.component.key": component_key,
        **get_protocol_span_attributes(),
        **get_auth_span_attributes(),
        **get_session_span_attributes(),
    }
    if resource_uri is not None:
        attrs["mcp.resource.uri"] = resource_uri
    if tool_name is not None:
        attrs["gen_ai.tool.name"] = tool_name
    if prompt_name is not None:
        attrs["gen_ai.prompt.name"] = prompt_name
    return attrs


def record_span_exception(span: Span, e: Exception) -> None:
    """Record an exception and error status on a span."""
    if span.is_recording():
        error_type = "tool_error" if isinstance(e, _ToolError) else type(e).__qualname__
        span.set_attribute("error.type", error_type)
        span.record_exception(e)
        span.set_status(Status(StatusCode.ERROR, str(e)))


@contextmanager
def seam_span(method: str, server_name: str) -> Generator[Span, None, None]:
    """Open the per-request SERVER span at the FastMCP middleware seam.

    The span is named after the method and carries the base MCP attributes
    (`mcp.method.name`, `fastmcp.server.name`, auth/session context) so
    seam-only methods (`logging/setLevel`, `tasks/*`, `ping`, `initialize`, ...)
    are fully attributed even though they never reach the high-level path. It is
    marked with `SEAM_SPAN_MARKER` so a later `server_span` call in the
    high-level path enriches this span with component attributes instead of
    opening a second one. Exceptions raised anywhere below the seam — including
    rejections *before* the high-level path (auth, not-found, middleware vetoes)
    that would otherwise produce no SERVER span at all — are recorded here.
    """
    attrs = {
        SEAM_SPAN_MARKER: True,
        "mcp.method.name": method,
        "fastmcp.server.name": server_name,
        **get_protocol_span_attributes(),
        **get_auth_span_attributes(),
        **get_session_span_attributes(),
    }
    tracer = get_tracer()
    with tracer.start_as_current_span(
        method,
        context=_get_parent_trace_context(),
        kind=SpanKind.SERVER,
        attributes=attrs,
    ) as span:
        # Restore: `attributes=attrs` above is what makes on_start hooks and
        # the sampler see these values at creation time (the whole point of
        # this helper). But OTel's Tracer.start_span builds the span from
        # `sampling_result.attributes`, not the `attributes` kwarg directly —
        # a custom Sampler whose SamplingResult.attributes defaults to None
        # silently drops everything we passed. This only fires when the span
        # ends up with no attributes at all, so any sampler that supplied
        # attributes of its own — forwarding ours, redacting or replacing
        # some, or substituting entirely its own — is left untouched, as is
        # an SDK attribute limit that evicted some.
        if span.is_recording():
            restore_dropped_attributes(span, attrs)
        token = _active_seam_span.set(span)
        try:
            yield span
        except Exception as e:
            record_span_exception(span, e)
            raise
        finally:
            _active_seam_span.reset(token)


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
    """Emit or enrich a SERVER span with standard MCP attributes and auth context.

    When the current active span is the request's seam span (opened by
    `FastMCPServerMiddleware` and marked with `SEAM_SPAN_MARKER`), this sets the
    component attributes on that span and yields it *without* starting a second
    span — so failures rejected before this point and the successful high-level
    call share one richly-attributed SERVER span. Otherwise (non-seam contexts,
    e.g. in-process `mcp.call_tool()` calls that bypass the dispatcher) it opens a
    new SERVER span as before.

    Automatically records any exception on the span and sets error status.
    """
    attrs = _build_server_span_attrs(
        method,
        server_name,
        component_type,
        component_key,
        resource_uri,
        tool_name,
        prompt_name,
    )

    seam = _active_seam_span.get()
    active = get_current_span()
    if (
        seam is not None
        and seam is active
        and seam.is_recording()
        and seam.get_span_context().is_valid
    ):
        # Enrich the already-active seam span rather than opening a second one.
        seam.update_name(name)
        seam.set_attributes(attrs)
        try:
            yield seam
        except Exception as e:
            record_span_exception(seam, e)
            raise
        return

    tracer = get_tracer()
    with tracer.start_as_current_span(
        name,
        context=_get_parent_trace_context(),
        kind=SpanKind.SERVER,
        attributes=attrs,
    ) as span:
        # Restore for the same reason as `seam_span`: OTel builds the span
        # from `sampling_result.attributes`, which a custom Sampler may not
        # forward even though it was handed `attributes=attrs` above. Only
        # fires when the span ends up with no attributes at all.
        if span.is_recording():
            restore_dropped_attributes(span, attrs)
        try:
            yield span
        except Exception as e:
            record_span_exception(span, e)
            raise


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
    attrs: dict[str, str] = {
        "fastmcp.provider.type": provider_type,
        "fastmcp.component.key": component_key,
    }
    if method is not None:
        attrs["mcp.method.name"] = method

    tracer = get_tracer()
    with tracer.start_as_current_span(f"delegate {name}", attributes=attrs) as span:
        # Restore for the same reason as `seam_span`: OTel builds the span
        # from `sampling_result.attributes`, which a custom Sampler may not
        # forward even though it was handed `attributes=attrs` above. Only
        # fires when the span ends up with no attributes at all.
        if span.is_recording():
            restore_dropped_attributes(span, attrs)
        try:
            yield span
        except Exception as e:
            record_span_exception(span, e)
            raise


__all__ = [
    "SEAM_SPAN_MARKER",
    "delegate_span",
    "get_auth_span_attributes",
    "get_protocol_span_attributes",
    "get_session_span_attributes",
    "record_span_exception",
    "seam_span",
    "server_span",
]
