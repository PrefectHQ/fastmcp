import pytest
from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from starlette.routing import Route
from starlette.testclient import TestClient

from fastmcp.server import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair
from fastmcp.server.http import create_streamable_http_app


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
