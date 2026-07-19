"""End-to-end tests for session-owner credential enforcement.

A streamable-HTTP session is bound to the credential that created it. A request
that presents a different credential for the same ``Mcp-Session-Id`` is rejected
with 404, exactly as if the session did not exist. This closes a gap where a
leaked session id was usable by any bearer.

Enforcement lives in the SDK's ``StreamableHTTPSessionManager`` (the
``_session_owners`` map). It is active on FastMCP's stateful HTTP path because
the session manager owns the server lifecycle (its ``run()`` drives our
lifespan through ``_lifespan_proxy``).
"""

from starlette.testclient import TestClient

from fastmcp.server import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.http import StarletteWithLifespan, create_streamable_http_app

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

TOOLS_LIST_REQUEST = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {},
}

TOKEN_A = "token-a"
TOKEN_B = "token-b"

MCP_HEADERS = {"accept": "application/json, text/event-stream"}


def _make_app() -> StarletteWithLifespan:
    verifier = StaticTokenVerifier(
        tokens={
            TOKEN_A: {"client_id": "client-a", "scopes": []},
            TOKEN_B: {"client_id": "client-b", "scopes": []},
        }
    )
    server = FastMCP(name="OwnerEnforcementServer", auth=verifier)
    return create_streamable_http_app(
        server=server,
        streamable_http_path="/mcp",
        auth=verifier,
    )


def _initialize(client: TestClient, token: str) -> str:
    """Create a session with the given token and return its session id."""
    response = client.post(
        "/mcp",
        headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
        json=INITIALIZE_REQUEST,
    )
    assert response.status_code == 200
    session_id = response.headers.get("mcp-session-id")
    assert session_id is not None
    return session_id


def test_session_reuse_with_creating_credential_succeeds():
    app = _make_app()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = _initialize(client, TOKEN_A)

        response = client.post(
            "/mcp",
            headers={
                **MCP_HEADERS,
                "authorization": f"Bearer {TOKEN_A}",
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2024-11-05",
            },
            json=TOOLS_LIST_REQUEST,
        )

        assert response.status_code == 200


def test_session_reuse_with_different_credential_returns_404():
    """A session created with credential A must not be usable with credential B."""
    app = _make_app()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = _initialize(client, TOKEN_A)

        response = client.post(
            "/mcp",
            headers={
                **MCP_HEADERS,
                "authorization": f"Bearer {TOKEN_B}",
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2024-11-05",
            },
            json=TOOLS_LIST_REQUEST,
        )

        # Responds exactly as if the session did not exist.
        assert response.status_code == 404
