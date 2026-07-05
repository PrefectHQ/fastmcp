import pytest
from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from starlette.routing import Route
from starlette.testclient import TestClient

from fastmcp.server import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair
from fastmcp.server.http import create_streamable_http_app

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


class TestStreamableHTTPAppResourceMetadataURL:
    """Test resource_metadata_url logic in create_streamable_http_app."""

    @pytest.fixture
    def rsa_key_pair(self) -> RSAKeyPair:
        """Generate RSA key pair for testing."""
        return RSAKeyPair.generate()

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


class TestStreamableHTTPHostOriginProtection:
    """Test host and origin validation for streamable HTTP apps."""

    def test_rejects_untrusted_host_before_session_initialization(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
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

    def test_rejects_untrusted_origin_before_session_initialization(self):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
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
