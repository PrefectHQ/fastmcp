"""Tests for OAuth proxy UI and error page rendering."""

from unittest.mock import Mock

from starlette.requests import Request
from starlette.responses import HTMLResponse

from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.ui import create_error_html
from fastmcp.server.auth.providers.jwt import JWTVerifier


class TestErrorPageRendering:
    """Test error page rendering for OAuth callback errors."""

    def test_create_error_html_basic(self):
        """Test basic error page generation."""

        html = create_error_html(
            error_title="Test Error",
            error_message="This is a test error message",
        )

        # Verify it's valid HTML
        assert "<!DOCTYPE html>" in html
        assert "<title>Test Error</title>" in html
        assert "This is a test error message" in html
        assert 'class="info-box error"' in html

    def test_create_error_html_with_details(self):
        """Test error page with error details."""

        html = create_error_html(
            error_title="OAuth Error",
            error_message="Authentication failed",
            error_details={
                "Error Code": "invalid_scope",
                "Description": "Requested scope does not exist",
            },
        )

        # Verify error details are included
        assert "Error Details" in html
        assert "Error Code" in html
        assert "invalid_scope" in html
        assert "Description" in html
        assert "Requested scope does not exist" in html

    def test_create_error_html_escapes_user_input(self):
        """Test that error page properly escapes HTML in user input."""

        html = create_error_html(
            error_title="Error <script>alert('xss')</script>",
            error_message="Message with <b>HTML</b> tags",
            error_details={"Key<script>": "Value<img>"},
        )

        # Verify HTML is escaped
        assert "<script>alert('xss')</script>" not in html
        assert "&lt;script&gt;" in html
        assert "<b>HTML</b>" not in html
        assert "&lt;b&gt;HTML&lt;/b&gt;" in html

    async def test_callback_error_returns_html_page(self):
        """Test that OAuth callback errors return styled HTML instead of data: URLs."""
        # Create a minimal OAuth proxy
        provider = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=JWTVerifier(
                jwks_uri="https://idp.example.com/.well-known/jwks.json",
                issuer="https://idp.example.com",
                audience="test-client",
            ),
            base_url="http://localhost:8000",
            jwt_signing_key="test-signing-key",
        )

        # Mock a request with an error from the IdP
        mock_request = Mock(spec=Request)
        mock_request.query_params = {
            "error": "invalid_scope",
            "error_description": "The application asked for scope 'read' that doesn't exist",
            "state": "test-state",
        }

        # Call the callback handler
        response = await provider._handle_idp_callback(mock_request)

        # Verify we get an HTMLResponse, not a RedirectResponse
        assert isinstance(response, HTMLResponse)
        assert response.status_code == 400

        # Verify the response contains the error message
        assert b"invalid_scope" in response.body
        assert b"doesn&#x27;t exist" in response.body  # HTML-escaped apostrophe
        assert b"OAuth Error" in response.body
