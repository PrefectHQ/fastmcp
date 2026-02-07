"""Tests for OAuth proxy redirect URI validation."""

from unittest.mock import patch

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.shared.auth import InvalidRedirectUriError
from pydantic import AnyHttpUrl, AnyUrl

from fastmcp.server.auth.auth import TokenVerifier
from fastmcp.server.auth.cimd import CIMDDocument
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient

# Standard public IP used for DNS mocking in tests
TEST_PUBLIC_IP = "93.184.216.34"


class MockTokenVerifier(TokenVerifier):
    """Mock token verifier for testing."""

    def __init__(self):
        self.required_scopes = []

    async def verify_token(self, token: str) -> dict | None:  # type: ignore[override]
        return {"sub": "test-user"}


class TestProxyDCRClient:
    """Test ProxyDCRClient redirect URI validation."""

    def test_default_allows_all(self):
        """Test that default configuration allows all URIs for DCR compatibility."""
        client = ProxyDCRClient(
            client_id="test",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:3000")],
        )

        # All URIs should be allowed by default for DCR compatibility
        assert client.validate_redirect_uri(AnyUrl("http://localhost:3000")) == AnyUrl(
            "http://localhost:3000"
        )
        assert client.validate_redirect_uri(AnyUrl("http://localhost:8080")) == AnyUrl(
            "http://localhost:8080"
        )
        assert client.validate_redirect_uri(AnyUrl("http://127.0.0.1:3000")) == AnyUrl(
            "http://127.0.0.1:3000"
        )
        assert client.validate_redirect_uri(AnyUrl("http://example.com")) == AnyUrl(
            "http://example.com"
        )
        assert client.validate_redirect_uri(
            AnyUrl("https://claude.ai/api/mcp/auth_callback")
        ) == AnyUrl("https://claude.ai/api/mcp/auth_callback")

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
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(
                AnyUrl("cursor://anysphere.cursor-mcp/oauth/callback")
            )

    def test_default_not_applied_when_custom_patterns_supplied(self):
        """Test that default validation is not applied when custom patterns are supplied."""
        allowed_patterns = [
            "cursor://anysphere.cursor-mcp/oauth/callback",
            "https://app.example.com/*",
        ]

        client = ProxyDCRClient(
            client_id="test",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:3000")],
            allowed_redirect_uri_patterns=allowed_patterns,
        )

        assert client.validate_redirect_uri(
            AnyUrl("https://app.example.com/oauth/callback")
        )
        assert client.validate_redirect_uri(
            AnyUrl("cursor://anysphere.cursor-mcp/oauth/callback")
        )

        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://localhost:3000"))
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://127.0.0.1:3000"))
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("https://example.com"))

    def test_empty_list_allows_none(self):
        """Test that empty pattern list allows no URIs."""
        client = ProxyDCRClient(
            client_id="test",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:3000")],
            allowed_redirect_uri_patterns=[],
        )

        # Nothing should be allowed (except the pre-registered redirect_uris via fallback)
        # Pre-registered URI should work via fallback to base validation
        assert client.validate_redirect_uri(AnyUrl("http://localhost:3000"))

        # Non-registered URIs should be rejected
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://example.com"))
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("https://anywhere.com:9999/path"))
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://localhost:5000"))

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

    def test_cimd_none_redirect_uri_single_exact(self):
        """CIMD clients may omit redirect_uri only when a single exact URI exists."""
        cimd_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:3000/callback"],
        )
        client = ProxyDCRClient(
            client_id="https://example.com/client.json",
            client_secret=None,
            redirect_uris=None,
            cimd_document=cimd_doc,
        )

        result = client.validate_redirect_uri(None)
        assert result == AnyUrl("http://localhost:3000/callback")

    def test_cimd_none_redirect_uri_respects_proxy_patterns(self):
        """CIMD fallback redirect_uri must still satisfy proxy allowlist patterns."""
        cimd_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["https://evil.com/callback"],
        )
        client = ProxyDCRClient(
            client_id="https://example.com/client.json",
            client_secret=None,
            redirect_uris=None,
            cimd_document=cimd_doc,
            allowed_redirect_uri_patterns=["http://localhost:*"],
        )

        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(None)

    def test_cimd_none_redirect_uri_wildcard_rejected(self):
        """CIMD clients must specify redirect_uri when only wildcard patterns exist."""
        cimd_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:*/callback"],
        )
        client = ProxyDCRClient(
            client_id="https://example.com/client.json",
            client_secret=None,
            redirect_uris=None,
            cimd_document=cimd_doc,
        )

        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(None)

    def test_cimd_empty_proxy_allowlist_rejects_redirect_uri(self):
        """An explicit empty proxy allowlist should reject all CIMD redirect URIs."""
        cimd_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:3000/callback"],
        )
        client = ProxyDCRClient(
            client_id="https://example.com/client.json",
            client_secret=None,
            redirect_uris=None,
            cimd_document=cimd_doc,
            allowed_redirect_uri_patterns=[],
        )

        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://localhost:3000/callback"))


class TestOAuthProxyRedirectValidation:
    """Test OAuth proxy with redirect URI validation."""

    def test_proxy_default_allows_all(self):
        """Test that OAuth proxy defaults to allowing all URIs for DCR compatibility."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        # The proxy should store None for default (allow all)
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
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        assert proxy._allowed_client_redirect_uris == custom_patterns

    def test_proxy_empty_list_validation(self):
        """Test OAuth proxy with empty list (allow none)."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            allowed_client_redirect_uris=[],
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        assert proxy._allowed_client_redirect_uris == []

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
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
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
        registered = await proxy.get_client(
            "new-client"
        )  # Use the client ID we registered
        assert isinstance(registered, ProxyDCRClient)
        assert registered.allowed_redirect_uri_patterns == custom_patterns

    async def test_proxy_unregistered_client_returns_none(self):
        """Test that unregistered clients return None."""
        custom_patterns = ["http://localhost:*", "http://127.0.0.1:*"]

        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            allowed_client_redirect_uris=custom_patterns,
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        # Get an unregistered client
        client = await proxy.get_client("unknown-client")
        assert client is None


class TestOAuthProxyCIMDClient:
    """Test that CIMD clients obtained via proxy carry their document and apply dual validation."""

    @pytest.fixture
    def mock_dns(self):
        """Mock DNS resolution to return test public IP."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=[TEST_PUBLIC_IP],
        ):
            yield

    async def test_proxy_get_client_returns_cimd_client(self, httpx_mock, mock_dns):
        """CIMD client obtained via proxy's get_client has cimd_document attached."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "CIMD App",
            "redirect_uris": ["http://localhost:*/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "200"},
        )

        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        client = await proxy.get_client(url)
        assert isinstance(client, ProxyDCRClient)
        assert client.cimd_document is not None
        assert client.cimd_document.client_name == "CIMD App"
        assert client.client_id == url

    async def test_proxy_cimd_dual_redirect_validation(self, httpx_mock, mock_dns):
        """CIMD client from proxy enforces both CIMD redirect_uris and proxy patterns."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Dual Validation App",
            "redirect_uris": [
                "http://localhost:3000/callback",
                "https://evil.com/callback",
            ],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "200"},
        )

        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            allowed_client_redirect_uris=["http://localhost:*"],
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        client = await proxy.get_client(url)
        assert client is not None

        # In CIMD AND matches proxy pattern → accepted
        assert client.validate_redirect_uri(AnyUrl("http://localhost:3000/callback"))

        # In CIMD but NOT in proxy pattern → rejected
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("https://evil.com/callback"))

        # NOT in CIMD but matches proxy pattern → rejected
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://localhost:9999/other"))
