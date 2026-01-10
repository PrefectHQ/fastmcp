"""Ping middleware for keeping client connections alive."""

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
        """Start ping task on first message from a session."""
        if (
            context.fastmcp_context is None
            or context.fastmcp_context.request_context is None
        ):
            return await call_next(context)

        session = context.fastmcp_context.session
        session_id = id(session)

        async with self._lock:
            if session_id not in self._active_sessions:
                # _subscription_task_group is added by MiddlewareServerSession
                tg = session._subscription_task_group  # type: ignore[attr-defined]
                if tg is not None:
                    self._active_sessions.add(session_id)
                    tg.start_soon(self._ping_loop, session, session_id)

        return await call_next(context)

    async def _ping_loop(self, session: Any, session_id: int) -> None:
        """Send periodic pings until session ends."""
        try:
            while True:
                await anyio.sleep(self.interval_ms / 1000)
                await session.send_ping()
        finally:
            self._active_sessions.discard(session_id)
