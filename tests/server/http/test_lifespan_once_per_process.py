"""Regression test for driving the FastMCP lifespan through the SDK session manager.

Before FastMCP handed its lifespan to the SDK lowlevel Server (PR #4446), the
SDK v1 lifespan was effectively session-scoped and FastMCP worked around it by
driving its own ``_lifespan_manager`` beside the session manager. The SDK v2
``StreamableHTTPSessionManager`` now enters ``app.lifespan(app)`` -- FastMCP's
``_lifespan_proxy`` -- exactly once for the manager's lifetime and reuses the
yielded state for every session. The user lifespan must therefore fire once per
process and persist across HTTP client sessions, not once per session.

The invariant that actually matters is "the session manager drives the lifespan
exactly once, regardless of how many client sessions connect." A plain
user-lifespan enter/exit counter cannot guard it: ``_lifespan_manager`` is
ref-counted, and ``run_http_async`` opens an *outer* ``_lifespan_manager``
around uvicorn. That outer entry holds the ref count at >= 1 for the whole
server lifetime, so even if the session manager regressed to re-entering
``_lifespan_proxy`` once per session, the user lifespan would still be entered
exactly once (the proxy's nested ``_lifespan_manager`` entries would all reuse
the outer result). The user counter would stay ``1`` while the behavior it
claims to guard was broken.

So this test spies on the session-manager entry point directly -- it counts how
many times the SDK enters ``server._mcp_server.lifespan`` (the ``_lifespan_proxy``
wrapper) -- and asserts that count is exactly one across sequential and
overlapping sessions. A regression that moves ``app.lifespan(app)`` into the
per-session code path makes this count grow with the session count and fails
loudly. The user enter/exit counter is kept as a secondary check on the
"entered once, exited once at shutdown" shape.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.lowlevel.server import Server as LowLevelServer

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.utilities.tests import run_server_async


async def test_http_user_lifespan_fires_once_across_sessions():
    """The session manager must drive the user lifespan exactly once for the
    server process, even when several independent HTTP client sessions connect
    and disconnect.
    """
    enter_count = 0
    exit_count = 0

    @asynccontextmanager
    async def counting_lifespan(mcp: FastMCP) -> AsyncIterator[dict[str, Any]]:
        nonlocal enter_count, exit_count
        enter_count += 1
        try:
            yield {"initialized": True}
        finally:
            exit_count += 1

    server = FastMCP("LifespanOnceServer", lifespan=counting_lifespan)

    @server.tool
    def ping() -> str:
        return "pong"

    # Spy on the session manager's single entry point into FastMCP's lifespan.
    # `StreamableHTTPSessionManager.run()` calls `self.app.lifespan(self.app)`
    # exactly once and reuses the yielded state per session; `self.app` is
    # `server._mcp_server`, so `server._mcp_server.lifespan` is the
    # `_lifespan_proxy` wrapper. Counting entries here asserts the invariant
    # directly, independent of `_lifespan_manager`'s ref-count masking.
    proxy_enter_count = 0
    original_lifespan = server._mcp_server.lifespan

    @asynccontextmanager
    async def counting_proxy(app: LowLevelServer[Any]) -> AsyncIterator[Any]:
        nonlocal proxy_enter_count
        proxy_enter_count += 1
        async with original_lifespan(app) as state:
            yield state

    server._mcp_server.lifespan = counting_proxy

    async with run_server_async(server, transport="http") as mcp_url:
        # `run_server_async` yields a URL that already includes the `/mcp` path.
        # Three separate, sequential client sessions against the same process.
        for _ in range(3):
            async with Client(StreamableHttpTransport(mcp_url)) as client:
                result = await client.call_tool("ping", {})
                assert result.data == "pong"
            # The session manager must not re-drive the lifespan when a session
            # closes -- it owns a single entry for the whole process lifetime.
            assert proxy_enter_count == 1
            assert exit_count == 0

        # Overlapping sessions must also observe a single, still-open lifespan.
        async with (
            Client(StreamableHttpTransport(mcp_url)) as c1,
            Client(StreamableHttpTransport(mcp_url)) as c2,
        ):
            assert (await c1.call_tool("ping", {})).data == "pong"
            assert (await c2.call_tool("ping", {})).data == "pong"
            assert proxy_enter_count == 1
            assert exit_count == 0

    # The session manager entered the lifespan exactly once across every
    # session, and the user lifespan was entered once and exited once at
    # process shutdown.
    assert proxy_enter_count == 1
    assert enter_count == 1
    assert exit_count == 1
