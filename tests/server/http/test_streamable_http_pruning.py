import time
from unittest.mock import AsyncMock

import anyio
import pytest

from fastmcp.server import FastMCP
from fastmcp.server.http import ClearingStreamableHTTPSessionManager


@pytest.mark.anyio
async def test_streamable_http_pruning():
    """Verify that ClearingStreamableHTTPSessionManager tracks and prunes inactive transports."""
    mcp = FastMCP("PruningTest")
    app = mcp.http_app(path="/mcp")

    # Use short intervals for testing
    ClearingStreamableHTTPSessionManager.session_timeout = 0.1
    ClearingStreamableHTTPSessionManager.prune_interval = 0.05

    fake_transport = AsyncMock()
    fake_transport.terminate = AsyncMock()
    fake_transport._request_streams = {}  # 0 active streams

    async with app.router.lifespan_context(app):
        sm = None
        for route in app.router.routes:
            endpoint = getattr(route, "endpoint", None)
            sm = getattr(endpoint, "session_manager", None)
            if sm is not None:
                break

        assert sm is not None
        assert isinstance(sm, ClearingStreamableHTTPSessionManager)

        # Register a fake transport with activity in the past
        fake_transport._last_activity_time = time.time() - 10.0
        sm._server_instances["fake-session"] = fake_transport

        # Wait for the background prune loop (should only need ~0.1-0.2 seconds now)
        await anyio.sleep(0.2)

        # The fake transport should have been terminated and removed
        fake_transport.terminate.assert_awaited_once()
        assert "fake-session" not in sm._server_instances
