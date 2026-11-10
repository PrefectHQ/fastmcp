from collections.abc import MutableMapping
from typing import Any, Literal

import httpx2
import pytest
from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from pydantic import AnyHttpUrl
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from fastmcp.server import FastMCP
from fastmcp.server.auth import (
    AuthProvider,
    MultiAuth,
    RemoteAuthProvider,
    TokenVerifier,
)
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.http import (
    HostOriginGuardMiddleware,
    StarletteWithLifespan,
    create_sse_app,
    create_streamable_http_app,
)

INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "attacker", "version": "0.1"},
    },
}


async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    response = Response("OK")
    await response(scope, receive, send)


async def _empty_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _guard_status(
    *,
    host: str,
    origin: str | None = None,
    server: tuple[str, int] | None = None,
    mode: Literal["auto", "strict"] = "auto",
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> int:
    app = HostOriginGuardMiddleware(
        _ok_app,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        mode=mode,
    )
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": server,
    }
    sent_messages: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent_messages.append(message)

    await app(scope, _empty_receive, send)
    response_start = next(
        message for message in sent_messages if message["type"] == "http.response.start"
    )
    return response_start["status"]


class TestStreamableHTTPAppResourceMetadataURL:
    """Test resource_metadata_url logic in create_streamable_http_app."""

    @pytest.fixture
    def bearer_auth_provider(self, rsa_key_pair):
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://issuer",
            audience="https://audience",
            base_url="https://resource.example.com",
        )
        return provider

    def test_auth_endpoint_wrapped_with_require_auth_middleware(
        self, bearer_auth_provider
    ):
        """Test that auth-protected endpoints use RequireAuthMiddleware."""
        server = FastMCP(name="TestServer")

        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=bearer_auth_provider,
        )

        route = next(r for r in app.routes if isinstance(r, Route) and r.path == "/mcp")

        # When auth is enabled, endpoint should use RequireAuthMiddleware
        assert isinstance(route.endpoint, RequireAuthMiddleware)

    def test_auth_endpoint_has_correct_methods(self, rsa_key_pair):
        """Test that auth-protected endpoints have correct HTTP methods."""
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://issuer",
            audience="https://audience",
            base_url="https://resource.example.com/",
        )
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=provider,
        )
        route = next(r for r in app.routes if isinstance(r, Route) and r.path == "/mcp")

        # Verify RequireAuthMiddleware is applied
        assert isinstance(route.endpoint, RequireAuthMiddleware)
        # Verify methods include GET, POST, DELETE for streamable-http
        expected_methods = {"GET", "POST", "DELETE"}
        assert route.methods is not None
        assert expected_methods.issubset(set(route.methods))

    def test_no_auth_provider_mounts_without_middleware(self, rsa_key_pair):
        """Test that endpoints without auth are not wrapped with middleware."""
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=None,
        )
        route = next(r for r in app.routes if isinstance(r, Route) and r.path == "/mcp")
        # Without auth, no RequireAuthMiddleware should be applied
        assert not isinstance(route.endpoint, RequireAuthMiddleware)

    def test_authenticated_requests_still_require_auth(self, bearer_auth_provider):
        """Test that actual requests (not OPTIONS) still require authentication."""
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=bearer_auth_provider,
        )

        # Test POST request without auth - should fail with 401
        with TestClient(app) as client:
            response = client.post("/mcp")
            assert response.status_code == 401
            assert "www-authenticate" in response.headers


class _VerifierOnly(TokenVerifier):
    """A token verifier that serves no routes and accepts no token."""

    async def verify_token(self, token: str) -> AccessToken | None:
        return None


_PROTECTED_PATH = {"http": "/mcp", "sse": "/sse"}


def _auth_app(
    transport: Literal["http", "sse"], auth: AuthProvider
) -> StarletteWithLifespan:
    server = FastMCP(name="TestServer")
    if transport == "sse":
        return create_sse_app(
            server=server, message_path="/messages/", sse_path="/sse", auth=auth
        )
    return create_streamable_http_app(
        server=server, streamable_http_path="/mcp", auth=auth
    )


class TestAuthChallengeResourceMetadata:
    """The 401 challenge names protected resource metadata only when it is served."""

    @pytest.mark.parametrize("transport", ["http", "sse"])
    @pytest.mark.parametrize("url_parameter", ["base_url", "resource_base_url"])
    @pytest.mark.parametrize("shape", ["verifier", "multi_auth"])
    async def test_verifier_only_challenge_omits_unserved_resource_metadata(
        self,
        transport: Literal["http", "sse"],
        url_parameter: str,
        shape: str,
    ):
        url = "https://resource.example.com"
        base_url = url if url_parameter == "base_url" else None
        resource_base_url = url if url_parameter == "resource_base_url" else None
        auth: AuthProvider
        if shape == "verifier":
            auth = _VerifierOnly(base_url=base_url, resource_base_url=resource_base_url)
        else:
            auth = MultiAuth(
                verifiers=[_VerifierOnly()],
                base_url=base_url,
                resource_base_url=resource_base_url,
            )
        app = _auth_app(transport, auth)
        path = _PROTECTED_PATH[transport]

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="https://resource.example.com",
        ) as client:
            missing = await client.get(path)
            invalid = await client.get(path, headers={"Authorization": "Bearer x"})
            metadata = await client.get(f"/.well-known/oauth-protected-resource{path}")

        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert invalid.status_code == 401
        assert "resource_metadata=" not in invalid.headers["www-authenticate"]
        assert metadata.status_code == 404

    @pytest.mark.parametrize("transport", ["http", "sse"])
    async def test_challenge_names_resource_metadata_the_provider_serves(
        self, transport: Literal["http", "sse"]
    ):
        auth = RemoteAuthProvider(
            token_verifier=_VerifierOnly(),
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://resource.example.com",
        )
        app = _auth_app(transport, auth)
        path = _PROTECTED_PATH[transport]
        metadata_url = (
            f"https://resource.example.com/.well-known/oauth-protected-resource{path}"
        )

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="https://resource.example.com",
        ) as client:
            missing = await client.get(path)
            metadata = await client.get(metadata_url)

        assert missing.status_code == 401
        assert (
            missing.headers["www-authenticate"]
            == f'Bearer resource_metadata="{metadata_url}"'
        )
        assert metadata.status_code == 200
        assert metadata.json()["resource"] == f"https://resource.example.com{path}"


class TestStreamableHTTPHostOriginProtection:
    """Test host and origin validation for streamable HTTP apps."""

    def test_default_allows_untrusted_host_for_compatibility(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            allowed_hosts=["apps.example.com"],
        )

        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "host": "internal-upstream",
                    "x-forwarded-host": "apps.example.com",
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 200
        assert "mcp-session-id" in response.headers

    async def test_auto_allows_public_host_when_server_scope_is_ambiguous(self):
        status = await _guard_status(
            host="mcp.example.com",
            origin="https://app.example.com",
            server=None,
        )

        assert status == 200

    async def test_auto_rejects_untrusted_host_when_server_scope_is_loopback(self):
        status = await _guard_status(
            host="attacker.example",
            origin="https://attacker.example",
            server=("127.0.0.1", 8000),
        )

        assert status == 421

    async def test_strict_rejects_public_host_when_server_scope_is_ambiguous(self):
        status = await _guard_status(
            host="mcp.example.com",
            origin="https://app.example.com",
            server=None,
            mode="strict",
        )

        assert status == 421

    async def test_auto_rejects_same_origin_fallback_without_trusted_host_boundary(
        self,
    ):
        status = await _guard_status(
            host="attacker.example",
            origin="https://attacker.example",
            server=None,
            allowed_origins=["https://app.example.com"],
        )

        assert status == 403

    async def test_auto_allows_configured_origin_without_trusted_host_boundary(self):
        status = await _guard_status(
            host="mcp.example.com",
            origin="https://app.example.com",
            server=None,
            allowed_origins=["https://app.example.com"],
        )

        assert status == 200

    def test_auto_rejects_untrusted_host_before_session_initialization(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            host_origin_protection="auto",
        )

        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "host": "attacker.example",
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 421
        assert "mcp-session-id" not in response.headers

    def test_auto_rejects_untrusted_origin_before_session_initialization(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            host_origin_protection="auto",
        )

        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "origin": "https://attacker.example",
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 403
        assert "mcp-session-id" not in response.headers

    def test_allows_configured_host_and_origin(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            host_origin_protection="auto",
            allowed_hosts=["mcp.example.com"],
            allowed_origins=["https://app.example.com"],
        )

        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "host": "mcp.example.com",
                    "origin": "https://app.example.com",
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 200
        assert "mcp-session-id" in response.headers

    def test_allows_same_request_origin(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            host_origin_protection="auto",
            allowed_hosts=["mcp.example.com"],
        )

        with TestClient(app, base_url="https://mcp.example.com") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "origin": "https://mcp.example.com",
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 200
        assert "mcp-session-id" in response.headers

    def test_allows_loopback_origin_for_loopback_host(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            host_origin_protection="auto",
        )

        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "origin": "http://localhost:3000",
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 200
        assert "mcp-session-id" in response.headers

    def test_rejects_loopback_origin_for_public_host(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            host_origin_protection="auto",
            allowed_hosts=["mcp.example.com"],
        )

        with TestClient(app, base_url="https://mcp.example.com") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "origin": "http://localhost:3000",
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 403
        assert "mcp-session-id" not in response.headers

    def test_allows_configured_loopback_origin_for_public_host(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            host_origin_protection="auto",
            allowed_hosts=["mcp.example.com"],
            allowed_origins=["http://localhost:3000"],
        )

        with TestClient(app, base_url="https://mcp.example.com") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "origin": "http://localhost:3000",
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 200
        assert "mcp-session-id" in response.headers

    @pytest.mark.parametrize(
        "origin",
        [
            "http://mcp.example.com",
            "https://mcp.example.com:3000",
        ],
    )
    def test_rejects_same_host_different_origin(self, origin: str):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            host_origin_protection="auto",
            allowed_hosts=["mcp.example.com"],
        )

        with TestClient(app, base_url="https://mcp.example.com") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "origin": origin,
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 403
        assert "mcp-session-id" not in response.headers

    def test_can_disable_host_origin_protection(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            host_origin_protection=False,
        )

        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "host": "attacker.example",
                    "origin": "https://attacker.example",
                },
                json=INITIALIZE_REQUEST,
            )

        assert response.status_code == 200
        assert "mcp-session-id" in response.headers
