"""Tests for OAuth proxy client registration (DCR)."""

from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl


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
