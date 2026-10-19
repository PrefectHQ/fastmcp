"""Client-side dependency helpers."""


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
