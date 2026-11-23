"""Tests for the streamable-HTTP ``session_idle_timeout`` setting.

An idle session is terminated after ``session_idle_timeout`` seconds of
inactivity. The deadline is reset on every request. This is the SDK's
behavior, surfaced through FastMCP's ``http_session_idle_timeout`` setting.
"""

import time
from typing import Any

import pytest
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.testclient import TestClient

from fastmcp.server import FastMCP
from fastmcp.server.http import (
    StarletteWithLifespan,
    StreamableHTTPASGIApp,
    create_streamable_http_app,
)
from fastmcp.utilities.tests import temporary_settings

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


@pytest.mark.parametrize("use_http_app", [True, False])
def test_default_timeout_reaches_session_manager(use_http_app: bool):
    server = FastMCP(name="DefaultTimeoutServer")
    app = (
        server.http_app(path="/mcp")
        if use_http_app
        else create_streamable_http_app(server, "/mcp")
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE_REQUEST)
        assert response.status_code == 200
        sm = _find_session_manager(app)
        assert sm is not None
        assert (
            sm.session_idle_timeout
            == StreamableHTTPSessionManager(server._mcp_server).session_idle_timeout
        )


@pytest.mark.parametrize("use_http_app", [True, False])
def test_idle_timeout_can_be_disabled(use_http_app: bool):
    server = FastMCP(name="NoIdleTimeoutServer")
    with temporary_settings(http_session_idle_timeout=None):
        app = (
            server.http_app(path="/mcp")
            if use_http_app
            else create_streamable_http_app(server, "/mcp", session_idle_timeout=None)
        )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE_REQUEST)
        assert response.status_code == 200
        sm = _find_session_manager(app)
        assert sm is not None
        assert sm.session_idle_timeout is None
        assert response.headers["mcp-session-id"] in sm._server_instances


@pytest.mark.parametrize("use_http_app", [True, False])
def test_default_timeout_supports_stateless_app(use_http_app: bool):
    server = FastMCP(name="StatelessServer")
    with temporary_settings(stateless_http=True):
        app = (
            server.http_app(path="/mcp")
            if use_http_app
            else create_streamable_http_app(server, "/mcp", stateless_http=True)
        )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE_REQUEST)
        assert response.status_code == 200
        assert "mcp-session-id" not in response.headers
        sm = _find_session_manager(app)
        assert sm is not None
        assert not sm._server_instances


def test_http_app_timeout_overrides_setting():
    with temporary_settings(http_session_idle_timeout=1800):
        app = FastMCP(name="TimeoutOverrideServer").http_app(session_idle_timeout=60)

    with TestClient(app):
        sm = _find_session_manager(app)
        assert sm is not None
        assert sm.session_idle_timeout == 60


@pytest.mark.parametrize("use_http_app", [True, False])
def test_default_timeout_frees_session_capacity(use_http_app: bool, monkeypatch):
    server = FastMCP(name="SessionCapacityServer")
    if not hasattr(StreamableHTTPSessionManager(server._mcp_server), "max_sessions"):
        pytest.skip("The session capacity limit was added in MCP SDK 2.2")

    sdk_init = StreamableHTTPSessionManager.__init__

    def small_session_pool(
        self, *args: Any, session_idle_timeout: float | None = 0.5, **kwargs: Any
    ):
        # Shorten only the SDK default, preserving an explicit None from FastMCP.
        sdk_init(
            self,
            *args,
            session_idle_timeout=session_idle_timeout,
            **{**kwargs, "max_sessions": 1},
        )

    monkeypatch.setattr(StreamableHTTPSessionManager, "__init__", small_session_pool)
    app = (
        server.http_app(path="/mcp")
        if use_http_app
        else create_streamable_http_app(server, "/mcp")
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE_REQUEST)
        assert response.status_code == 200
        session_id = response.headers["mcp-session-id"]
        response = client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE_REQUEST)
        assert response.status_code == 503

        sm = _find_session_manager(app)
        assert sm is not None
        deadline = time.monotonic() + 3.0
        while session_id in sm._server_instances and time.monotonic() < deadline:
            time.sleep(0.02)
        assert session_id not in sm._server_instances

        response = client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE_REQUEST)
        assert response.status_code == 200
        assert response.headers["mcp-session-id"] != session_id


@pytest.mark.parametrize("use_http_app", [True, False])
def test_idle_session_is_terminated_after_timeout(use_http_app: bool):
    server = FastMCP(name="IdleTimeoutServer")
    with temporary_settings(http_session_idle_timeout=0.1):
        app = (
            server.http_app(path="/mcp")
            if use_http_app
            else create_streamable_http_app(
                server=server,
                streamable_http_path="/mcp",
                session_idle_timeout=0.1,
            )
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
