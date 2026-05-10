"""Client-side dependency helpers."""


def get_http_headers(
    include_all: bool = False,
    include: set[str] | None = None,
) -> dict[str, str]:
    """Return HTTP headers from an ambient server request, when available.

    The standalone client package has no server request context. Full FastMCP
    installs override this path through the compatibility package and provide
    the request-aware implementation from `fastmcp.server.dependencies`.
    """
    return {}
