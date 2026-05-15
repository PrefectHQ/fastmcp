"""Tests for OAuth proxy client registration (DCR)."""

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette

from fastmcp.server.auth.oauth_proxy.models import InvalidRedirectUriError


class TestOAuthProxyClientRegistration:
    """Tests for OAuth proxy client registration (DCR)."""

    async def test_register_client(self, oauth_proxy):
        """Test client registration creates ProxyDCRClient."""
        client_info = OAuthClientInformationFull(
            client_id="original-client",
            client_secret="original-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        await oauth_proxy.register_client(client_info)

        # Client should be retrievable with original credentials
        stored = await oauth_proxy.get_client("original-client")
        assert stored is not None
        assert stored.client_id == "original-client"
        # Proxy uses token_endpoint_auth_method="none", so client_secret is not stored
        assert stored.client_secret is None

    async def test_get_registered_client(self, oauth_proxy):
        """Test retrieving a registered client."""
        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:8080/callback")],
        )
        await oauth_proxy.register_client(client_info)

        retrieved = await oauth_proxy.get_client("test-client")
        assert retrieved is not None
        assert retrieved.client_id == "test-client"

    async def test_get_unregistered_client_returns_none(self, oauth_proxy):
        """Test that unregistered clients return None."""
        client = await oauth_proxy.get_client("unknown-client")
        assert client is None

    async def test_enforcing_allowed_redirect_uris(self, oauth_proxy):
        """Test enforcing allowed redirect uris configuration."""

        oauth_proxy._allowed_client_redirect_uris = ["http://localhost:12345/callback"]

        client_info = OAuthClientInformationFull(
            client_id="original-client",
            client_secret="original-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        await oauth_proxy.register_client(client_info)
        retrieved = await oauth_proxy.get_client("original-client")
        assert retrieved.allowed_redirect_uri_patterns == [
            "http://localhost:12345/callback"
        ]

        oauth_proxy._allowed_client_redirect_uris = [
            "http://localhost:12345/updated_callback"
        ]

        retrieved = await oauth_proxy.get_client("original-client")
        assert retrieved.allowed_redirect_uri_patterns == [
            "http://localhost:12345/updated_callback"
        ]

    async def test_update_default_scopes_applies_to_dcr_registration(self, oauth_proxy):
        """DCR clients without scope should receive the updated default scopes."""
        oauth_proxy.update_default_scopes(["read", "write", "calendar"])

        app = Starlette(routes=oauth_proxy.get_routes())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://myserver.com",
        ) as client:
            response = await client.post(
                "/register",
                json={
                    "redirect_uris": ["https://client.example.com/callback"],
                    "client_name": "Test Client",
                },
            )

        assert response.status_code == 201
        client_info = response.json()
        assert client_info["scope"] == "read write calendar"

        registered_client = await oauth_proxy.get_client(client_info["client_id"])
        assert registered_client is not None
        assert registered_client.scope == "read write calendar"


class TestUpstreamClientIdFallback:
    """Tests for clients that skip DCR and use the upstream client_id directly."""

    async def test_upstream_client_id_returns_synthetic_client(self, oauth_proxy):
        """Clients that skip DCR and use upstream client_id directly are accepted."""
        # oauth_proxy fixture uses "test-client-id" as upstream_client_id
        client = await oauth_proxy.get_client("test-client-id")
        assert client is not None
        assert client.client_id == "test-client-id"
        assert client.client_secret is None
        assert client.token_endpoint_auth_method == "none"

    async def test_upstream_client_id_inherits_allowed_redirect_uris(self, oauth_proxy):
        """Synthetic upstream client respects the proxy's redirect URI restrictions."""
        oauth_proxy._allowed_client_redirect_uris = ["http://localhost:*"]
        client = await oauth_proxy.get_client("test-client-id")
        assert client is not None
        assert client.allowed_redirect_uri_patterns == ["http://localhost:*"]

    async def test_unknown_client_id_still_returns_none(self, oauth_proxy):
        """Non-upstream, unregistered IDs still return None."""
        client = await oauth_proxy.get_client("some-random-client-id")
        assert client is None

    async def test_redirect_uri_allowed_when_no_pattern_restriction(self, oauth_proxy):
        """Any redirect URI is accepted when allowed_client_redirect_uris is None."""
        assert oauth_proxy._allowed_client_redirect_uris is None
        client = await oauth_proxy.get_client("test-client-id")
        assert client is not None
        uri = client.validate_redirect_uri(AnyUrl("https://claude.ai/oauth/callback"))
        assert str(uri) == "https://claude.ai/oauth/callback"

    async def test_redirect_uri_validated_against_patterns(self, oauth_proxy):
        """Redirect URI validation honours allowed_client_redirect_uris when set."""
        oauth_proxy._allowed_client_redirect_uris = ["http://localhost:*"]
        client = await oauth_proxy.get_client("test-client-id")
        assert client is not None

        # Allowed URI passes
        uri = client.validate_redirect_uri(AnyUrl("http://localhost:12345/callback"))
        assert str(uri) == "http://localhost:12345/callback"

        # Disallowed URI raises
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("https://evil.example.com/callback"))

    async def test_redirect_uri_blocked_when_empty_allowlist(self, oauth_proxy):
        """Empty allowed_client_redirect_uris blocks all redirect URIs, including localhost."""
        oauth_proxy._allowed_client_redirect_uris = []
        client = await oauth_proxy.get_client("test-client-id")
        assert client is not None

        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://localhost/callback"))

        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("https://claude.ai/oauth/callback"))

    async def test_none_redirect_uri_validated_against_patterns(self, oauth_proxy):
        """redirect_uri=None resolves to the placeholder then validates against patterns."""
        # Placeholder is http://localhost — a pattern that can't match it forces rejection.
        oauth_proxy._allowed_client_redirect_uris = ["https://myapp.example.com/*"]
        client = await oauth_proxy.get_client("test-client-id")
        assert client is not None

        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(None)

    async def test_none_redirect_uri_rejected_when_empty_allowlist(self, oauth_proxy):
        """redirect_uri=None is rejected when allowlist is empty ([] blocks the resolved URI too)."""
        oauth_proxy._allowed_client_redirect_uris = []
        client = await oauth_proxy.get_client("test-client-id")
        assert client is not None

        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(None)
