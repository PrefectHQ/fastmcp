from __future__ import annotations

from typing import TYPE_CHECKING, ParamSpec, TypeVar

from starlette.requests import Request

if TYPE_CHECKING:
    from fastmcp.server.context import Context

P = ParamSpec("P")
R = TypeVar("R")


# --- Context ---


def get_context() -> Context:
    from fastmcp.server.context import _current_context

    context = _current_context.get()
    if context is None:
        raise RuntimeError("No active context found.")
    return context


# --- HTTP Request ---


def get_http_request() -> Request:
    from fastmcp.server.http import _current_http_request

    request = _current_http_request.get()
    if request is None:
        raise RuntimeError("No active HTTP request found.")
    return request


def get_http_headers(include_all: bool = False) -> dict[str, str]:
    """
    Extract headers from the current HTTP request if available.

    Never raises an exception, even if there is no active HTTP request (in which case
    an empty dict is returned).

    By default, strips problematic headers like `content-length` that cause issues if forwarded to downstream clients.
    If `include_all` is True, all headers are returned.
    """
    if include_all:
        exclude_headers = set()
    else:
        exclude_headers = {"content-length"}

    # ensure all lowercase!
    # (just in case)
    exclude_headers = {h.lower() for h in exclude_headers}

    headers = {}

    try:
        request = get_http_request()
        for name, value in request.headers.items():
            lower_name = name.lower()
            if lower_name not in exclude_headers:
                headers[lower_name] = str(value)
        return headers
    except RuntimeError:
        return {}
