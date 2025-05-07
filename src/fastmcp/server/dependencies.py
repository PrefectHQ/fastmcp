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
