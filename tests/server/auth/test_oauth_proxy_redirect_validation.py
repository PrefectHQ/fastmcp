"""Tests for OAuth proxy redirect URI validation."""

import pytest
from mcp.shared.auth import InvalidRedirectUriError
from pydantic import AnyUrl

from fastmcp.server.auth.auth import TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy, ProxyDCRClient


class MockTokenVerifier(TokenVerifier):
    """Mock token verifier for testing."""

    def __init__(self):
        self.required_scopes = []

    async def verify_token(self, token: str) -> dict | None:
        return {"sub": "test-user"}


class TestProxyDCRClient:
    """Test ProxyDCRClient redirect URI validation."""

    def test_default_localhost_only(self):
        """Test that default configuration only allows localhost."""
        client = ProxyDCRClient(
            client_id="test",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:3000")],
        )

        # Localhost should be allowed
        assert client.validate_redirect_uri(AnyUrl("http://localhost:3000")) == AnyUrl(
            "http://localhost:3000"
        )
        assert client.validate_redirect_uri(AnyUrl("http://localhost:8080")) == AnyUrl(
            "http://localhost:8080"
        )
        assert client.validate_redirect_uri(AnyUrl("http://127.0.0.1:3000")) == AnyUrl(
            "http://127.0.0.1:3000"
        )

        # Non-localhost should fallback to base validation
        # This will check against registered redirect_uris
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://example.com"))

    def test_custom_patterns(self):
        """Test custom redirect URI patterns."""
        client = ProxyDCRClient(
            client_id="test",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:3000")],
            allowed_redirect_uri_patterns=[
                "http://localhost:*",
                "https://app.example.com/*",
            ],
        )

        # Allowed by patterns
        assert client.validate_redirect_uri(AnyUrl("http://localhost:3000"))
        assert client.validate_redirect_uri(AnyUrl("https://app.example.com/callback"))

        # Not allowed by patterns - will fallback to base validation
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://127.0.0.1:3000"))

    def test_empty_list_allows_all(self):
        """Test that empty pattern list allows all URIs."""
        client = ProxyDCRClient(
            client_id="test",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:3000")],
            allowed_redirect_uri_patterns=[],
        )

        # Everything should be allowed
        assert client.validate_redirect_uri(AnyUrl("http://localhost:3000"))
        assert client.validate_redirect_uri(AnyUrl("http://example.com"))
        assert client.validate_redirect_uri(AnyUrl("https://anywhere.com:9999/path"))

    def test_none_redirect_uri(self):
        """Test that None redirect URI uses default behavior."""
        client = ProxyDCRClient(
            client_id="test",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:3000")],
        )

        # None should use the first registered URI
        result = client.validate_redirect_uri(None)
        assert result == AnyUrl("http://localhost:3000")


class TestOAuthProxyRedirectValidation:
    """Test OAuth proxy with redirect URI validation."""

    def test_proxy_default_localhost_validation(self):
        """Test that OAuth proxy defaults to localhost-only validation."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
        )

        # The proxy should store None for default localhost patterns
        assert proxy._allowed_client_redirect_uris is None

    def test_proxy_custom_patterns(self):
        """Test OAuth proxy with custom redirect patterns."""
        custom_patterns = ["http://localhost:*", "https://*.myapp.com/*"]

        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            allowed_client_redirect_uris=custom_patterns,
        )

        assert proxy._allowed_client_redirect_uris == custom_patterns

    def test_proxy_empty_list_validation(self):
        """Test OAuth proxy with empty list (allow all)."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            allowed_client_redirect_uris=[],
        )

        assert proxy._allowed_client_redirect_uris == []

    @pytest.mark.asyncio
    async def test_proxy_register_client_uses_patterns(self):
        """Test that registered clients use the configured patterns."""
        custom_patterns = ["https://app.example.com/*"]

        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            allowed_client_redirect_uris=custom_patterns,
        )

        # Register a client
        from mcp.shared.auth import OAuthClientInformationFull

        client_info = OAuthClientInformationFull(
            client_id="new-client",
            client_secret="new-secret",
            redirect_uris=[AnyUrl("https://app.example.com/callback")],
        )

        await proxy.register_client(client_info)

        # Get the registered client
        registered = await proxy.get_client("test-client")  # Uses upstream ID
        assert isinstance(registered, ProxyDCRClient)
        assert registered._allowed_redirect_uri_patterns == custom_patterns

    @pytest.mark.asyncio
    async def test_proxy_unregistered_client_uses_patterns(self):
        """Test that unregistered clients also use configured patterns."""
        custom_patterns = ["http://localhost:*", "http://127.0.0.1:*"]

        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            allowed_client_redirect_uris=custom_patterns,
        )

        # Get an unregistered client
        client = await proxy.get_client("unknown-client")
        assert isinstance(client, ProxyDCRClient)
        assert client._allowed_redirect_uri_patterns == custom_patterns
