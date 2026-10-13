"""`methods` is an allowlist, including when it is empty.

`LoggingMiddleware(methods=[])` asks for no method to be logged, and both
constructors document `methods` as "List of methods to log. If None, logs all
methods." The filter tested truthiness, so an empty list was indistinguishable
from `None` and every message was logged instead.
"""

from __future__ import annotations

import io
import logging

from fastmcp import Client, FastMCP
from fastmcp.server.middleware.logging import (
    LoggingMiddleware,
    StructuredLoggingMiddleware,
)


def _capture() -> tuple[io.StringIO, logging.Logger]:
    stream = io.StringIO()
    logger = logging.getLogger("test.logging.methods")
    logger.handlers = [logging.StreamHandler(stream)]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return stream, logger


def _server(middleware: object) -> FastMCP:
    mcp = FastMCP("methods-filter", middleware=[middleware])  # type: ignore[list-item]

    @mcp.tool
    def ping() -> str:
        return "pong"

    return mcp


async def _call(mcp: FastMCP) -> None:
    async with Client(mcp) as client:
        await client.call_tool("ping", {})


async def test_empty_methods_logs_nothing() -> None:
    stream, logger = _capture()

    await _call(_server(LoggingMiddleware(methods=[], logger=logger)))

    assert stream.getvalue() == ""


async def test_none_methods_logs_everything() -> None:
    stream, logger = _capture()

    await _call(_server(LoggingMiddleware(methods=None, logger=logger)))

    assert "tools/call" in stream.getvalue()


async def test_allowlist_restricts_to_listed_methods() -> None:
    stream, logger = _capture()

    await _call(_server(LoggingMiddleware(methods=["tools/list"], logger=logger)))

    assert "tools/call" not in stream.getvalue()


async def test_structured_middleware_shares_the_filter() -> None:
    stream, logger = _capture()

    await _call(_server(StructuredLoggingMiddleware(methods=[], logger=logger)))

    assert stream.getvalue() == ""
