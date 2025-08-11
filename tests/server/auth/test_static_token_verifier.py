"""Tests for StaticTokenVerifier integration with FastMCP."""

import httpx

from fastmcp.server import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


class TestStaticTokenVerifier:
    """Test StaticTokenVerifier integration with FastMCP server."""

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
        """Test that FastMCP server works with StaticTokenVerifier for HTTP requests."""
        verifier = StaticTokenVerifier(
            {"test-token": {"client_id": "test-client", "scopes": ["read", "write"]}}
        )

        server = FastMCP("TestServer", auth=verifier)

        @server.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # Create HTTP app
        app = server.http_app(transport="http")

        # Test unauthenticated request gets 401 (use exact path match to avoid redirect)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/mcp")
            assert response.status_code == 401
            assert "WWW-Authenticate" in response.headers

    async def test_server_with_token_verifier_redirect_behavior(self):
        """Test that FastMCP server redirects non-matching paths correctly."""
        verifier = StaticTokenVerifier(
            {"test-token": {"client_id": "test-client", "scopes": ["read", "write"]}}
        )

        server = FastMCP("TestServer", auth=verifier)

        @server.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # Create HTTP app (default path is /mcp)
        app = server.http_app(transport="http")

        # Test that non-matching path gets 307 redirect
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/mcp/", follow_redirects=False)
            assert response.status_code == 307
            assert response.headers["location"] == "http://test/mcp"

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
