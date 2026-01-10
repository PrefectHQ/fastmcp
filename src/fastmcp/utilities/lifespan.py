"""Lifespan utilities for combining async context manager lifespans."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any, TypeVar

AppT = TypeVar("AppT")


def combine_lifespans(
    *lifespans: Callable[[AppT], AbstractAsyncContextManager[dict[str, Any] | None]],
) -> Callable[[AppT], AbstractAsyncContextManager[dict[str, Any]]]:
    """Combine multiple lifespans into a single lifespan.

    Useful when mounting FastMCP into FastAPI and you need to run
    both your app's lifespan and the MCP server's lifespan.

    Works with both FastAPI-style lifespans (yield None) and FastMCP-style
    lifespans (yield dict). Results are merged; later lifespans override
    earlier ones on key conflicts.

    Lifespans are entered in order and exited in reverse order (LIFO).

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.utilities.lifespan import combine_lifespans
        from fastapi import FastAPI

        mcp = FastMCP("Tools")
        mcp_app = mcp.http_app()

        app = FastAPI(lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan))
        app.mount("/mcp", mcp_app)  # MCP endpoint at /mcp
        ```

    Args:
        *lifespans: Lifespan context manager factories to combine.

    Returns:
        A combined lifespan context manager factory.
    """

    @asynccontextmanager
    async def combined(app: AppT) -> AsyncIterator[dict[str, Any]]:
        merged: dict[str, Any] = {}
        async with AsyncExitStack() as stack:
            for ls in lifespans:
                result = await stack.enter_async_context(ls(app))
                if result is not None:
                    merged.update(result)
            yield merged

    return combined
