"""Ping middleware for keeping client connections alive."""

import asyncio
import contextlib
from typing import Any

import anyio

from .middleware import CallNext, Middleware, MiddlewareContext


class PingMiddleware(Middleware):
    """Middleware that sends periodic pings to keep client connections alive.

    Starts a background ping task on first message from each session. The task
    sends server-to-client pings at the configured interval until the session
    ends.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.middleware import PingMiddleware

        mcp = FastMCP("MyServer")
        mcp.add_middleware(PingMiddleware(interval_ms=5000))
        ```
    """

    def __init__(self, interval_ms: int = 30000):
        """Initialize ping middleware.

        Args:
            interval_ms: Interval between pings in milliseconds (default: 30000)

        Raises:
            ValueError: If interval_ms is not positive
        """
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        self.interval_ms = interval_ms
        self._active_sessions: set[int] = set()
        self._lock = anyio.Lock()

    async def on_message(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Start ping task on first message from a connection."""
        if (
            context.fastmcp_context is None
            or context.fastmcp_context.request_context is None
        ):
            return await call_next(context)

        session = context.fastmcp_context.session
        # SDK v2 constructs a ServerSession per request; the stable per-connection
        # identity lives on the underlying Connection. Key the keepalive loop off
        # it so one ping task runs for the whole connection and is torn down when
        # the connection closes.
        connection = getattr(session, "_connection", None)
        connection_id = id(connection) if connection is not None else id(session)

        async with self._lock:
            if connection_id not in self._active_sessions:
                self._active_sessions.add(connection_id)
                ping_task = asyncio.create_task(
                    self._ping_loop(session, connection_id),
                    name=f"ping-keepalive-{connection_id}",
                )

                if connection is not None:

                    async def _cancel_ping() -> None:
                        ping_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await ping_task

                    connection.exit_stack.push_async_callback(_cancel_ping)

        return await call_next(context)

    async def _ping_loop(self, session: Any, connection_id: int) -> None:
        """Send periodic pings until the connection ends."""
        try:
            while True:
                await anyio.sleep(self.interval_ms / 1000)
                try:
                    await session.send_ping()
                except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                    return
        finally:
            self._active_sessions.discard(connection_id)
