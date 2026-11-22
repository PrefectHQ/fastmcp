"""Client-side dependency helpers."""

from collections.abc import Mapping


def _merge_forwardable_http_headers(configured: Mapping[str, str]) -> dict[str, str]:
    """Combine forwardable ambient headers with a transport's configured headers.

    Configured headers take precedence. Header names are case-insensitive, so a
    configured `Authorization` replaces the forwarded `authorization` instead of
    being sent alongside it.
    """
    configured_names = {name.lower() for name in configured}
    forwarded = {
        name: value
        for name, value in _get_forwardable_http_headers().items()
        if name.lower() not in configured_names
    }
    return forwarded | dict(configured)


def _get_forwardable_http_headers() -> dict[str, str]:
    """Return ambient headers safe to copy onto a new MCP connection.

    MCP transport and routing headers describe one HTTP hop and must be
    regenerated for the new connection. `Last-Event-ID` likewise belongs to
    the inbound connection's event stream. Other headers, including
    authorization and custom proxy headers, are preserved.
    """
    return {
        name: value
        for name, value in get_http_headers(include={"authorization"}).items()
        if not name.startswith("mcp-") and name != "last-event-id"
    }


def get_http_headers(
    include_all: bool = False,
    include: set[str] | None = None,
) -> dict[str, str]:
    """Return HTTP headers from an ambient server request, when available.

    The standalone client package has no server request context. When the full
    FastMCP package is installed, delegate to its request-aware implementation.
    """
    try:
        from fastmcp.server.dependencies import (
            get_http_headers as get_server_http_headers,
        )
    except ImportError:
        return {}

    return get_server_http_headers(include_all=include_all, include=include)
