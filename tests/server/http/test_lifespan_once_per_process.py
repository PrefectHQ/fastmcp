"""Regression test for driving the FastMCP lifespan through the SDK session manager.

Before FastMCP handed its lifespan to the SDK lowlevel Server (PR #4446), the
SDK v1 lifespan was effectively session-scoped and FastMCP worked around it by
driving its own ``_lifespan_manager`` beside the session manager. The SDK v2
``StreamableHTTPSessionManager`` now enters ``app.lifespan(app)`` exactly once
for the manager's lifetime, so the user lifespan must fire once per process and
persist across multiple HTTP client sessions -- not once per session.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.utilities.tests import run_server_async


async def test_http_user_lifespan_fires_once_across_sessions():
    """The user lifespan must be entered exactly once for the server process,
    even when several independent HTTP client sessions connect and disconnect.
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

    async with run_server_async(server, transport="http") as mcp_url:
        # `run_server_async` yields a URL that already includes the `/mcp` path.
        # Three separate, sequential client sessions against the same process.
        for _ in range(3):
            async with Client(StreamableHttpTransport(mcp_url)) as client:
                result = await client.call_tool("ping", {})
                assert result.data == "pong"
            # The lifespan must not have exited when a session closed -- it is
            # owned by the session manager for the whole process lifetime.
            assert enter_count == 1
            assert exit_count == 0

        # Overlapping sessions must also observe a single, still-open lifespan.
        async with (
            Client(StreamableHttpTransport(mcp_url)) as c1,
            Client(StreamableHttpTransport(mcp_url)) as c2,
        ):
            assert (await c1.call_tool("ping", {})).data == "pong"
            assert (await c2.call_tool("ping", {})).data == "pong"
            assert enter_count == 1
            assert exit_count == 0

    # After the server process task is torn down, the lifespan has exited once.
    # (A spurious re-entry during teardown would re-exit, so this also guards
    # that the lifespan was entered exactly once.)
    assert exit_count == 1
