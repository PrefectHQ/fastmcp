from __future__ import annotations

from contextlib import (
    asynccontextmanager,
)
from contextvars import ContextVar

from starlette.requests import Request

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


_current_starlette_request: ContextVar[Request | None] = ContextVar(
    "starlette_request",
    default=None,
)


@asynccontextmanager
async def starlette_request_context(request: Request):
    token = _current_starlette_request.set(request)
    try:
        yield
    finally:
        _current_starlette_request.reset(token)


def get_current_starlette_request() -> Request | None:
    return _current_starlette_request.get()


class RequestMiddleware:
    """
    Middleware that stores each request in a ContextVar
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async with starlette_request_context(Request(scope)):
            await self.app(scope, receive, send)
