import pytest
from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from starlette.routing import Route

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
            resource_server_url="https://resource.example.com",
        )
        return provider

    def test_require_auth_middleware_receives_resource_metadata_url(
        self, bearer_auth_provider
    ):
        server = FastMCP(name="TestServer")

        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=bearer_auth_provider,
        )

        route = next(r for r in app.routes if isinstance(r, Route) and r.path == "/mcp")

        assert isinstance(route.endpoint, RequireAuthMiddleware)
        assert (
            str(route.endpoint.resource_metadata_url)
            == "https://resource.example.com/.well-known/oauth-protected-resource"
        )

    def test_trailing_slash_handling_in_resource_server_url(self, rsa_key_pair):
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://issuer",
            audience="https://audience",
            resource_server_url="https://resource.example.com/",
        )
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=provider,
        )
        route = next(r for r in app.routes if isinstance(r, Route) and r.path == "/mcp")
        assert isinstance(route.endpoint, RequireAuthMiddleware)
        # Should not have double slash
        assert (
            str(route.endpoint.resource_metadata_url)
            == "https://resource.example.com/.well-known/oauth-protected-resource"
        )

    def test_no_auth_provider_mounts_without_require_auth_middleware(
        self, rsa_key_pair
    ):
        server = FastMCP(name="TestServer")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=None,
        )
        route = next(r for r in app.routes if isinstance(r, Route) and r.path == "/mcp")
        assert not isinstance(route.endpoint, RequireAuthMiddleware)
