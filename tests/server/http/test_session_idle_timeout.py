"""Tests for the streamable-HTTP ``session_idle_timeout`` setting.

An idle session is terminated after ``session_idle_timeout`` seconds of
inactivity. The deadline is reset on every request. This is the SDK's
behavior, surfaced through FastMCP's ``http_session_idle_timeout`` setting.
"""

import time

from starlette.testclient import TestClient

from fastmcp.server import FastMCP
from fastmcp.server.http import (
    StarletteWithLifespan,
    StreamableHTTPASGIApp,
    create_streamable_http_app,
)

INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "client", "version": "0.1"},
    },
}

MCP_HEADERS = {"accept": "application/json, text/event-stream"}


def _find_session_manager(app: StarletteWithLifespan):
    for route in app.router.routes:
        endpoint = getattr(route, "endpoint", None)
        if isinstance(endpoint, StreamableHTTPASGIApp):
            return endpoint.session_manager
    return None


def test_idle_session_is_terminated_after_timeout():
    server = FastMCP(name="IdleTimeoutServer")
    app = create_streamable_http_app(
        server=server,
        streamable_http_path="/mcp",
        session_idle_timeout=0.1,
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE_REQUEST)
        assert response.status_code == 200
        session_id = response.headers.get("mcp-session-id")
        assert session_id is not None

        sm = _find_session_manager(app)
        assert sm is not None
        assert session_id in sm._server_instances

        # Wait past the idle deadline; the SDK's idle cancel scope fires and
        # removes the session from the active instances. Poll to stay fast.
        # The idle timeout itself is driven by anyio's event-loop clock
        # inside the SDK (not a mockable Python-level time source), so this
        # remains a real wait; the timeout and poll interval are kept as
        # small as reliably possible.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if session_id not in sm._server_instances:
                break
            time.sleep(0.02)

        assert session_id not in sm._server_instances

        # The now-expired session id is rejected with 404.
        response = client.post(
            "/mcp",
            headers={
                **MCP_HEADERS,
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2024-11-05",
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert response.status_code == 404
