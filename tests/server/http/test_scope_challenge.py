"""Component scope shortfalls must challenge before MCP response headers."""

import pytest
from starlette.testclient import TestClient

from fastmcp import FastMCP
from fastmcp.server.auth import restrict_tag
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.http import create_streamable_http_app
from fastmcp.server.middleware import AuthMiddleware


@pytest.mark.parametrize("json_response", [True, False], ids=["json", "sse"])
@pytest.mark.parametrize("protocol", ["2024-11-05", "2026-07-28"])
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("tools/call", {"name": "write_note", "arguments": {}}),
        ("resources/read", {"uri": "notes://one"}),
        ("resources/read", {"uri": "notes://items/one"}),
        ("prompts/get", {"name": "write_prompt", "arguments": {}}),
    ],
)
def test_component_scope_challenge_precedes_dispatch(
    json_response: bool, protocol: str, method: str, params: dict[str, object]
) -> None:
    verifier = StaticTokenVerifier(
        tokens={
            "narrow": {"client_id": "client", "scopes": ["read"]},
            "wide": {"client_id": "client", "scopes": ["read", "notes:write"]},
        }
    )
    server = FastMCP(
        "Scope Challenge",
        auth=verifier,
        middleware=[AuthMiddleware(auth=restrict_tag("write", scopes=["notes:write"]))],
    )
    calls: list[str] = []

    @server.tool(tags={"write"})
    def write_note() -> str:
        calls.append("tool")
        return "written"

    @server.resource("notes://one", tags={"write"})
    def note() -> str:
        calls.append("resource")
        return "written"

    @server.resource("notes://items/{item}", tags={"write"})
    def note_template(item: str) -> str:
        calls.append("template")
        return item

    @server.prompt(tags={"write"})
    def write_prompt() -> str:
        calls.append("prompt")
        return "written"

    app = create_streamable_http_app(
        server=server,
        streamable_http_path="/mcp",
        auth=verifier,
        json_response=json_response,
    )
    accept = (
        "application/json" if json_response else "application/json, text/event-stream"
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        init = client.post(
            "/mcp",
            headers={"Authorization": "Bearer narrow", "Accept": accept},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": protocol,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert init.status_code == 200
        session_id = init.headers["mcp-session-id"]
        headers = {
            "Authorization": "Bearer narrow",
            "Accept": accept,
            "mcp-session-id": session_id,
            "mcp-protocol-version": protocol,
        }
        if protocol == "2024-11-05":
            unknown_session = client.post(
                "/mcp",
                headers={**headers, "mcp-session-id": "unknown"},
                json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
            )
            assert unknown_session.status_code == 404
        if protocol == "2026-07-28":
            malformed_headers = {
                **headers,
                "mcp-method": method,
                "mcp-name": str(params.get("name", params.get("uri", ""))),
            }
            malformed = client.post(
                "/mcp",
                headers=malformed_headers,
                json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
            )
            assert malformed.status_code == 400
            unsupported_params = {
                **params,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-08-01",
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            }
            unsupported = client.post(
                "/mcp",
                headers={**malformed_headers, "mcp-protocol-version": "2026-08-01"},
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": method,
                    "params": unsupported_params,
                },
            )
            assert unsupported.status_code != 403
            params = {
                **params,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": protocol,
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            }
            headers["mcp-method"] = method
            headers["mcp-name"] = str(params.get("name", params.get("uri", "")))
        request = {"jsonrpc": "2.0", "id": 2, "method": method, "params": params}
        if protocol == "2026-07-28":
            duplicated = client.post(
                "/mcp",
                headers=[*headers.items(), ("mcp-method", method)],
                json=request,
            )
            assert duplicated.status_code == 400
        unacceptable = client.post(
            "/mcp", headers={**headers, "Accept": "text/plain"}, json=request
        )
        assert unacceptable.status_code == 406
        denied = client.post("/mcp", headers=headers, json=request)
        assert denied.status_code == 403
        assert denied.headers["www-authenticate"] == (
            'Bearer error="insufficient_scope", scope="notes:write"'
        )
        assert calls == []

        headers["Authorization"] = "Bearer wide"
        allowed = client.post("/mcp", headers=headers, json=request)
        assert allowed.status_code == 200, allowed.text
        assert len(calls) == 1


def test_preflight_preserves_sdk_body_limit() -> None:
    verifier = StaticTokenVerifier(
        tokens={"token": {"client_id": "client", "scopes": ["read"]}}
    )
    server = FastMCP("Body limit", auth=verifier)
    app = create_streamable_http_app(
        server=server, streamable_http_path="/mcp", auth=verifier
    )
    headers = {
        "Authorization": "Bearer token",
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(app, base_url="http://127.0.0.1") as client:
        init = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert init.status_code == 200
        oversized = client.post(
            "/mcp",
            headers={
                **headers,
                "mcp-session-id": init.headers["mcp-session-id"],
                "mcp-protocol-version": "2024-11-05",
                "Content-Type": "application/json",
            },
            content=b"x" * (4 * 1024 * 1024 + 1),
        )
        assert oversized.status_code == 413

        deeply_nested = client.post(
            "/mcp",
            headers={
                **headers,
                "mcp-session-id": init.headers["mcp-session-id"],
                "mcp-protocol-version": "2024-11-05",
                "Content-Type": "application/json",
            },
            content=b"[" * 16000 + b"0" + b"]" * 16000,
        )
        assert deeply_nested.status_code == 400
