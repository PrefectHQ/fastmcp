"""Regression tests for graceful streamable-HTTP shutdown (issue #3025)."""

from unittest.mock import AsyncMock

from fastmcp.server import FastMCP


async def test_lifespan_terminates_active_transports_before_task_group_cancel():
    """Active streamable-HTTP transports must be terminated during lifespan
    shutdown so streaming responses end cleanly instead of being aborted by
    the session manager's task-group cancel.

    See PrefectHQ/fastmcp#3025: without graceful termination, Uvicorn logs
    "ASGI callable returned without completing response." on CTRL+C while a
    client holds an SSE GET stream open.
    """
    server = FastMCP(name="ShutdownTest")
    app = server.http_app(path="/mcp")

    fake_transport = AsyncMock()
    fake_transport.terminate = AsyncMock()

    async with app.router.lifespan_context(app):
        # The session manager is constructed inside the lifespan; once it has
        # started, register a fake active transport as if a client were
        # holding an SSE stream open.
        sm = None
        for route in app.router.routes:
            endpoint = getattr(route, "endpoint", None)
            sm = getattr(endpoint, "session_manager", None)
            if sm is not None:
                break
        assert sm is not None, "streamable-http session manager not initialised"
        sm._server_instances["fake-session"] = fake_transport

    fake_transport.terminate.assert_awaited_once()
