"""Client-side dependency helpers."""


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
