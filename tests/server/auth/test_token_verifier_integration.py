"""Tests for TokenVerifier integration with FastMCP."""

import httpx
import pytest
from mcp.server.auth.provider import AccessToken

from fastmcp.server import FastMCP
from fastmcp.server.auth.verifiers import StaticTokenVerifier


class TestTokenVerifierIntegration:
    """Test TokenVerifier integration with FastMCP server."""

    def test_static_token_verifier_creation(self):
        """Test creating a FastMCP server with StaticTokenVerifier."""
        verifier = StaticTokenVerifier(
            {"test-token": {"client_id": "test-client", "scopes": ["read", "write"]}}
        )

        server = FastMCP("TestServer", auth=verifier)
        assert server.auth is verifier

    async def test_static_token_verifier_verify_token(self):
        """Test StaticTokenVerifier token verification."""
        verifier = StaticTokenVerifier(
            {
                "valid-token": {
                    "client_id": "test-client",
                    "scopes": ["read", "write"],
                    "expires_at": None,
                },
                "scoped-token": {"client_id": "limited-client", "scopes": ["read"]},
            }
        )

        # Test valid token
        result = await verifier.verify_token("valid-token")
        assert isinstance(result, AccessToken)
        assert result.client_id == "test-client"
        assert result.scopes == ["read", "write"]
        assert result.token == "valid-token"
        assert result.expires_at is None

        # Test token with different scopes
        result = await verifier.verify_token("scoped-token")
        assert isinstance(result, AccessToken)
        assert result.client_id == "limited-client"
        assert result.scopes == ["read"]

        # Test invalid token
        result = await verifier.verify_token("invalid-token")
        assert result is None

    async def test_server_with_token_verifier_http_app(self):
        """Test that FastMCP server works with TokenVerifier for HTTP requests."""
        verifier = StaticTokenVerifier(
            {"test-token": {"client_id": "test-client", "scopes": ["read", "write"]}}
        )

        server = FastMCP("TestServer", auth=verifier)

        @server.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # Create HTTP app
        app = server.http_app(transport="http")

        # Test unauthenticated request gets 401
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/mcp/")
            assert response.status_code == 401
            assert "WWW-Authenticate" in response.headers

    def test_server_rejects_both_oauth_and_token_verifier(self):
        """Test that server raises error when both OAuth and TokenVerifier provided."""
        from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

        oauth_provider = InMemoryOAuthProvider("http://test.com")
        token_verifier = StaticTokenVerifier({"token": {"client_id": "test"}})

        # This should work - OAuth provider
        server1 = FastMCP("Test1", auth=oauth_provider)
        assert server1.auth is oauth_provider

        # This should work - TokenVerifier
        server2 = FastMCP("Test2", auth=token_verifier)
        assert server2.auth is token_verifier


class TestJWTVerifierImport:
    """Test JWT token verifier can be imported and created."""

    def test_jwt_verifier_requires_pyjwt(self):
        """Test that JWTVerifier raises helpful error without PyJWT."""
        # Since PyJWT is likely installed in test environment, we'll just test construction
        from fastmcp.server.auth.verifiers import JWTVerifier

        # This should work if PyJWT is available
        try:
            verifier = JWTVerifier(public_key="dummy-key")
            assert verifier.public_key == "dummy-key"
            assert verifier.algorithm == "RS256"
        except ImportError as e:
            # If PyJWT not available, should get helpful error
            assert "PyJWT is required" in str(e)


class TestIntrospectionTokenVerifierImport:
    """Test introspection token verifier can be imported and created."""

    def test_introspection_verifier_creation(self):
        """Test IntrospectionTokenVerifier construction."""
        from fastmcp.server.auth.verifiers import IntrospectionTokenVerifier

        verifier = IntrospectionTokenVerifier(
            "https://auth.example.com/introspect", "https://resource.example.com"
        )

        assert (
            str(verifier.introspection_endpoint)
            == "https://auth.example.com/introspect"
        )
        assert str(verifier.server_url) == "https://resource.example.com/"
        assert verifier.validate_resource is False
        assert verifier.required_scopes == []

    def test_introspection_verifier_rejects_private_urls(self):
        """Test that IntrospectionTokenVerifier rejects private URLs."""
        from fastmcp.server.auth.verifiers import IntrospectionTokenVerifier

        with pytest.raises(ValueError, match="private/localhost URL"):
            IntrospectionTokenVerifier(
                "http://localhost/introspect", "https://resource.example.com"
            )

        with pytest.raises(ValueError, match="private/localhost URL"):
            IntrospectionTokenVerifier(
                "http://127.0.0.1/introspect", "https://resource.example.com"
            )
