from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.requests import Request

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

_current_http_request: ContextVar[Request | None] = ContextVar(
    "http_request",
    default=None,
)


@contextmanager
def set_http_request(request: Request) -> Generator[Request, None, None]:
    token = _current_http_request.set(request)
    try:
        yield request
    finally:
        _current_http_request.reset(token)


class RequestContextMiddleware:
    """
    Middleware that stores each request in a ContextVar
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        with set_http_request(Request(scope)):
            await self.app(scope, receive, send)
