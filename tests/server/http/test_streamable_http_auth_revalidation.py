from __future__ import annotations

from starlette.testclient import TestClient

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token


PROTOCOL_VERSION = "2025-11-25"


class MutableTokenVerifier(TokenVerifier):
    """Token verifier whose accepted tokens can change during a session."""

    def __init__(self) -> None:
        super().__init__(base_url="https://auth.example.test")
        self.active_tokens: set[str] = set()
        self.calls: list[str] = []

    async def verify_token(self, token: str) -> AccessToken | None:
        self.calls.append(token)
        if token not in self.active_tokens:
            return None

        return AccessToken(
            token=token,
            client_id=f"client-{token}",
            scopes=[],
        )


def auth_headers(
    token: str,
    *,
    session_id: str | None = None,
    accept: str = "application/json",
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "Content-Type": "application/json",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    return headers


def create_authenticated_server(
    verifier: MutableTokenVerifier,
) -> FastMCP:
    server = FastMCP("Auth Revalidation Test", auth=verifier)

    @server.tool
    async def current_token() -> str:
        token = get_access_token()
        return token.token if token is not None else "no-token"

    return server


def initialize_session(client: TestClient, token: str) -> str:
    response = client.post(
        "/mcp",
        headers=auth_headers(token),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "auth-revalidation-test", "version": "1.0"},
            },
        },
    )

    assert response.status_code == 200
    return response.headers["mcp-session-id"]


def send_initialized(client: TestClient, token: str, session_id: str) -> None:
    response = client.post(
        "/mcp",
        headers=auth_headers(token, session_id=session_id),
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )

    assert response.status_code == 202


def call_current_token(client: TestClient, token: str, session_id: str):
    return client.post(
        "/mcp",
        headers=auth_headers(token, session_id=session_id),
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "current_token", "arguments": {}},
        },
    )


def test_streamable_http_same_session_uses_revalidated_token() -> None:
    verifier = MutableTokenVerifier()
    verifier.active_tokens.add("old-token")
    server = create_authenticated_server(verifier)
    app = server.http_app(path="/mcp", json_response=True)

    with TestClient(app) as client:
        session_id = initialize_session(client, "old-token")
        send_initialized(client, "old-token", session_id)

        verifier.active_tokens.remove("old-token")
        verifier.active_tokens.add("new-token")

        response = call_current_token(client, "new-token", session_id)

        assert response.status_code == 200
        assert response.json()["result"]["structuredContent"]["result"] == "new-token"
        assert verifier.calls == ["old-token", "old-token", "new-token"]


def test_streamable_http_reconnect_rejects_expired_session_token() -> None:
    verifier = MutableTokenVerifier()
    verifier.active_tokens.add("old-token")
    server = create_authenticated_server(verifier)
    app = server.http_app(path="/mcp", json_response=True)

    with TestClient(app) as client:
        session_id = initialize_session(client, "old-token")
        send_initialized(client, "old-token", session_id)

        verifier.active_tokens.remove("old-token")

        headers = auth_headers(
            "old-token",
            session_id=session_id,
            accept="text/event-stream",
        )
        headers["Last-Event-ID"] = "event-1"

        response = client.get(
            "/mcp",
            headers=headers,
        )

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_token"
        assert verifier.calls == ["old-token", "old-token", "old-token"]
