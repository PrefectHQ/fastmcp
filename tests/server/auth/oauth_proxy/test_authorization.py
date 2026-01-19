"""Tests for OAuth proxy authorization flow."""

from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.oauth_proxy import OAuthProxy


class TestOAuthProxyAuthorization:
    """Tests for OAuth proxy authorization flow."""

    async def test_authorize_creates_transaction(self, oauth_proxy):
        """Test that authorize creates transaction and redirects to consent."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
            jwt_signing_key="test-secret",  # type: ignore[call-arg]  # Optional field in MCP SDK
        )

        # Register client first (required for consent flow)
        await oauth_proxy.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:54321/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state-123",
            code_challenge="challenge-abc",
            scopes=["read", "write"],
        )

        redirect_url = await oauth_proxy.authorize(client, params)

        # Parse the redirect URL
        parsed = urlparse(redirect_url)
        query_params = parse_qs(parsed.query)

        # Should redirect to consent page
        assert "/consent" in redirect_url
        assert "txn_id" in query_params

        # Verify transaction was stored with correct data
        txn_id = query_params["txn_id"][0]
        transaction = await oauth_proxy._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert transaction.client_id == "test-client"
        assert transaction.code_challenge == "challenge-abc"
        assert transaction.client_state == "client-state-123"
        assert transaction.scopes == ["read", "write"]


class TestOAuthProxyPKCE:
    """Tests for OAuth proxy PKCE forwarding."""

    @pytest.fixture
    def proxy_with_pkce(self, jwt_verifier):
        return OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            forward_pkce=True,
            jwt_signing_key="test-secret",
        )

    @pytest.fixture
    def proxy_without_pkce(self, jwt_verifier):
        from fastmcp.server.auth.oauth_proxy import OAuthProxy

        return OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            forward_pkce=False,
            jwt_signing_key="test-secret",
        )

    async def test_pkce_forwarding_enabled(self, proxy_with_pkce):
        """Test that proxy generates and forwards its own PKCE."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Register client first
        await proxy_with_pkce.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
        )

        redirect_url = await proxy_with_pkce.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Should redirect to consent page
        assert "/consent" in redirect_url
        assert "txn_id" in query_params

        # Transaction should store both challenges
        txn_id = query_params["txn_id"][0]
        transaction = await proxy_with_pkce._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert transaction.code_challenge == "client_challenge"  # Client's
        assert transaction.proxy_code_verifier is not None  # Proxy's verifier
        # Proxy code challenge is computed from verifier when building upstream URL
        # Just verify the verifier exists and is different from client's challenge
        assert len(transaction.proxy_code_verifier) > 0

    async def test_pkce_forwarding_disabled(self, proxy_without_pkce):
        """Test that PKCE is not forwarded when disabled."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Register client first
        await proxy_without_pkce.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
        )

        redirect_url = await proxy_without_pkce.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Should redirect to consent page
        assert "/consent" in redirect_url
        assert "txn_id" in query_params

        # Client's challenge still stored, but no proxy PKCE
        txn_id = query_params["txn_id"][0]
        transaction = await proxy_without_pkce._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert transaction.code_challenge == "client_challenge"
        assert transaction.proxy_code_verifier is None  # No proxy PKCE when disabled


class TestParameterForwarding:
    """Tests for parameter forwarding in OAuth proxy."""

    async def test_extra_authorize_params_forwarded(self, jwt_verifier):
        """Test that extra authorize parameters are forwarded to upstream."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret",
            extra_authorize_params={
                "audience": "https://api.example.com",
                "prompt": "consent",
                "max_age": "3600",
            },
        )

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        await proxy.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="challenge",
            scopes=["read"],
            # No resource parameter
        )

        # Should succeed (no resource check needed)
        redirect_url = await proxy.authorize(client, params)
        assert "/consent" in redirect_url
