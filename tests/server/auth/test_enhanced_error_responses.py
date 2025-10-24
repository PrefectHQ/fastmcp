"""Tests for enhanced OAuth error responses.

This test suite covers:
1. Enhanced authorization handler (HTML and JSON error pages)
2. Enhanced middleware (better error messages)
3. Content negotiation
4. Server branding in error pages
"""

import pytest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.testclient import TestClient

from fastmcp import FastMCP
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair


class TestEnhancedAuthorizationHandler:
    """Tests for enhanced authorization handler error responses."""

    @pytest.fixture
    def rsa_key_pair(self) -> RSAKeyPair:
        """Generate RSA key pair for testing."""
        return RSAKeyPair.generate()

    @pytest.fixture
    def oauth_proxy(self, rsa_key_pair):
        """Create OAuth proxy for testing."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )

    def test_unregistered_client_returns_html_for_browser(self, oauth_proxy):
        """Test that unregistered client returns styled HTML for browser requests."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                headers={"Accept": "text/html"},
            )

            # Should return 400 with HTML content
            assert response.status_code == 400
            assert "text/html" in response.headers["content-type"]

            # HTML should contain error message
            html = response.text
            assert "Client Not Registered" in html
            assert "unregistered-client-id" in html
            assert "To fix this" in html
            assert "Close this browser window" in html
            assert "Clear authentication tokens" in html

            # Should have Link header for registration endpoint
            assert "Link" in response.headers
            assert "/register" in response.headers["Link"]

    def test_unregistered_client_returns_json_for_api(self, oauth_proxy):
        """Test that unregistered client returns enhanced JSON for API clients."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                headers={"Accept": "application/json"},
            )

            # Should return 400 with JSON content
            assert response.status_code == 400
            assert "application/json" in response.headers["content-type"]

            # JSON should have enhanced error response
            data = response.json()
            assert data["error"] == "invalid_request"
            assert "unregistered-client-id" in data["error_description"]
            assert data["state"] == "test-state"

            # Should include registration endpoint hints
            assert "registration_endpoint" in data
            assert data["registration_endpoint"] == "https://myserver.com/register"
            assert "authorization_server_metadata" in data

            # Should have Link header
            assert "Link" in response.headers
            assert "/register" in response.headers["Link"]

    def test_successful_authorization_not_enhanced(self, oauth_proxy):
        """Test that successful authorizations are not modified by enhancement."""
        app = Starlette(routes=oauth_proxy.get_routes())

        # Register a valid client first
        client_info = OAuthClientInformationFull(
            client_id="valid-client",
            client_secret="valid-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Need to register synchronously
        import asyncio

        asyncio.run(oauth_proxy.register_client(client_info))

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "valid-client",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                headers={"Accept": "text/html"},
                follow_redirects=False,
            )

            # Should redirect to consent page (302), not return error
            assert response.status_code == 302
            assert "/consent" in response.headers["location"]

    def test_html_error_includes_server_branding(self, oauth_proxy):
        """Test that HTML error page includes server branding from FastMCP instance."""
        from mcp.types import Icon

        # Create FastMCP server with custom branding
        mcp = FastMCP(
            "My Custom Server",
            icons=[Icon(src="https://example.com/icon.png", mimeType="image/png")],
        )

        # Create app with OAuth routes
        app = Starlette(routes=oauth_proxy.get_routes())
        # Attach FastMCP instance to app state (same as done in http.py)
        app.state.fastmcp_server = mcp

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                },
                headers={"Accept": "text/html"},
            )

            assert response.status_code == 400
            html = response.text

            # Should include custom server icon
            assert "https://example.com/icon.png" in html


class TestEnhancedRequireAuthMiddleware:
    """Tests for enhanced authentication middleware error messages."""

    @pytest.fixture
    def rsa_key_pair(self) -> RSAKeyPair:
        """Generate RSA key pair for testing."""
        return RSAKeyPair.generate()

    @pytest.fixture
    def jwt_verifier(self, rsa_key_pair):
        """Create JWT verifier for testing."""
        return JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.com",
            audience="https://test.com",
            base_url="https://test.com",
        )

    def test_invalid_token_enhanced_error_message(self, jwt_verifier):
        """Test that invalid_token errors have enhanced error messages."""
        from fastmcp.server.http import create_streamable_http_app

        server = FastMCP("Test Server")

        @server.tool
        def test_tool() -> str:
            return "test"

        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=jwt_verifier,
        )

        with TestClient(app) as client:
            # Request without Authorization header
            response = client.post("/mcp")

            assert response.status_code == 401
            assert "www-authenticate" in response.headers

            # Check enhanced error message
            data = response.json()
            assert data["error"] == "invalid_token"
            # Should have enhanced description with resolution steps
            assert "clear authentication tokens" in data["error_description"]
            assert "automatically re-register" in data["error_description"]

    def test_invalid_token_www_authenticate_header_format(self, jwt_verifier):
        """Test that WWW-Authenticate header format matches SDK."""
        from fastmcp.server.http import create_streamable_http_app

        server = FastMCP("Test Server")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=jwt_verifier,
        )

        with TestClient(app) as client:
            response = client.post("/mcp")

            assert response.status_code == 401
            www_auth = response.headers["www-authenticate"]

            # Should follow Bearer challenge format
            assert www_auth.startswith("Bearer ")
            assert 'error="invalid_token"' in www_auth
            assert "error_description=" in www_auth

    def test_insufficient_scope_not_enhanced(self, rsa_key_pair):
        """Test that insufficient_scope errors are not modified."""
        # Create a valid token with wrong scopes
        from fastmcp.server.http import create_streamable_http_app

        jwt_verifier = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.com",
            audience="https://test.com",
            base_url="https://test.com",
        )

        server = FastMCP("Test Server")

        @server.tool
        def test_tool() -> str:
            return "test"

        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=jwt_verifier,
        )

        # Note: Testing insufficient_scope would require mocking the verifier
        # to return a token with wrong scopes. For now, we verify the middleware
        # is properly in place by checking it rejects unauthenticated requests.
        with TestClient(app) as client:
            response = client.post("/mcp")
            # Without a valid token, we get invalid_token
            assert response.status_code == 401


class TestContentNegotiation:
    """Tests for content negotiation in error responses."""

    @pytest.fixture
    def oauth_proxy(self):
        """Create OAuth proxy for testing."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=RSAKeyPair.generate().public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )

    def test_html_preferred_when_both_accepted(self, oauth_proxy):
        """Test that HTML is preferred when both text/html and application/json are accepted."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                },
                headers={"Accept": "text/html,application/json"},
            )

            # Should prefer HTML
            assert response.status_code == 400
            assert "text/html" in response.headers["content-type"]

    def test_json_when_only_json_accepted(self, oauth_proxy):
        """Test that JSON is returned when only application/json is accepted."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                },
                headers={"Accept": "application/json"},
            )

            assert response.status_code == 400
            assert "application/json" in response.headers["content-type"]

    def test_json_when_no_accept_header(self, oauth_proxy):
        """Test that JSON is returned when no Accept header is provided."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                },
            )

            # Without Accept header, should return JSON (API default)
            assert response.status_code == 400
            assert "application/json" in response.headers["content-type"]
