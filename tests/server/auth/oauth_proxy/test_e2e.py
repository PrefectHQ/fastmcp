"""End-to-end tests for OAuth proxy using mock provider."""

import time
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp import FastMCP
from fastmcp.server.auth.auth import RefreshToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import ClientCode
from tests.server.auth.oauth_proxy.conftest import MockTokenVerifier


class TestOAuthProxyE2E:
    """End-to-end tests using mock OAuth provider."""

    async def test_full_oauth_flow_with_mock_provider(self, mock_oauth_provider):
        """Test complete OAuth flow with mock provider."""
        # Create proxy pointing to mock provider
        proxy = OAuthProxy(
            upstream_authorization_endpoint=mock_oauth_provider.authorize_endpoint,
            upstream_token_endpoint=mock_oauth_provider.token_endpoint,
            upstream_client_id="mock-client",
            upstream_client_secret="mock-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            jwt_signing_key="test-secret",
        )

        # Create FastMCP server with proxy
        server = FastMCP("Test Server", auth=proxy)

        @server.tool
        def protected_tool() -> str:
            return "Protected data"

        # Start authorization flow
        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Register client first
        await proxy.register_client(client_info)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="",  # Empty string for no PKCE
            scopes=["read"],
        )

        # Get authorization URL (now returns consent redirect)
        auth_url = await proxy.authorize(client_info, params)

        # Should redirect to consent page
        assert "/consent" in auth_url
        query_params = parse_qs(urlparse(auth_url).query)
        assert "txn_id" in query_params

        # Verify transaction was created with correct configuration
        txn_id = query_params["txn_id"][0]
        transaction = await proxy._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert transaction.client_id == "test-client"
        assert transaction.scopes == ["read"]
        # Transaction ID itself is used as upstream state parameter
        assert transaction.txn_id == txn_id

    async def test_token_refresh_with_mock_provider(self, mock_oauth_provider):
        """Test token refresh flow with mock provider."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint=mock_oauth_provider.authorize_endpoint,
            upstream_token_endpoint=mock_oauth_provider.token_endpoint,
            upstream_client_id="mock-client",
            upstream_client_secret="mock-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            jwt_signing_key="test-secret",
        )

        # Initialize JWT issuer before token operations
        proxy.set_mcp_path("/mcp")

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Register client first
        await proxy.register_client(client)

        # Set up initial upstream tokens in mock provider
        upstream_refresh_token = "mock_refresh_initial"
        mock_oauth_provider.refresh_tokens[upstream_refresh_token] = {
            "client_id": "mock-client",
            "scope": "read write",
        }

        with patch(
            "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client"
        ) as MockClient:
            mock_client = AsyncMock()

            # Mock initial token exchange to get FastMCP tokens
            mock_client.fetch_token = AsyncMock(
                return_value={
                    "access_token": "upstream-access-initial",
                    "refresh_token": upstream_refresh_token,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )

            # Configure mock to call real provider for refresh
            async def mock_refresh(*args, **kwargs):
                async with httpx.AsyncClient() as http:
                    response = await http.post(
                        mock_oauth_provider.token_endpoint,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": upstream_refresh_token,
                        },
                    )
                    return response.json()

            mock_client.refresh_token = mock_refresh
            MockClient.return_value = mock_client

            # Store client code that would be created during OAuth callback
            client_code = ClientCode(
                code="test-auth-code",
                client_id="test-client",
                redirect_uri="http://localhost:12345/callback",
                code_challenge="",
                code_challenge_method="S256",
                scopes=["read", "write"],
                idp_tokens={
                    "access_token": "upstream-access-initial",
                    "refresh_token": upstream_refresh_token,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
                expires_at=time.time() + 300,
                created_at=time.time(),
            )
            await proxy._code_store.put(key=client_code.code, value=client_code)

            # Exchange authorization code to get FastMCP tokens
            auth_code = AuthorizationCode(
                code="test-auth-code",
                scopes=["read", "write"],
                expires_at=time.time() + 300,
                client_id="test-client",
                code_challenge="",
                redirect_uri=AnyUrl("http://localhost:12345/callback"),
                redirect_uri_provided_explicitly=True,
            )
            initial_result = await proxy.exchange_authorization_code(
                client=client,
                authorization_code=auth_code,
            )

            # Now test refresh with the valid FastMCP refresh token
            assert initial_result.refresh_token is not None
            fastmcp_refresh = RefreshToken(
                token=initial_result.refresh_token,
                client_id="test-client",
                scopes=["read"],
                expires_at=None,
            )

            result = await proxy.exchange_refresh_token(
                client, fastmcp_refresh, ["read"]
            )

            # Should return new FastMCP tokens (not upstream tokens)
            assert result.access_token != "upstream-access-initial"
            # FastMCP tokens are JWTs (have 3 segments)
            assert len(result.access_token.split(".")) == 3
            assert mock_oauth_provider.refresh_called

    async def test_pkce_validation_with_mock_provider(self, mock_oauth_provider):
        """Test PKCE validation with mock provider."""
        mock_oauth_provider.require_pkce = True

        proxy = OAuthProxy(
            upstream_authorization_endpoint=mock_oauth_provider.authorize_endpoint,
            upstream_token_endpoint=mock_oauth_provider.token_endpoint,
            upstream_client_id="mock-client",
            upstream_client_secret="mock-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
            forward_pkce=True,  # Enable PKCE forwarding
            jwt_signing_key="test-secret",
        )

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Register client first
        await proxy.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge_value",
            scopes=["read"],
        )

        # Start authorization with PKCE
        auth_url = await proxy.authorize(client, params)
        query_params = parse_qs(urlparse(auth_url).query)

        # Should redirect to consent page
        assert "/consent" in auth_url
        assert "txn_id" in query_params

        # Transaction should have proxy's PKCE verifier (different from client's)
        txn_id = query_params["txn_id"][0]
        transaction = await proxy._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert (
            transaction.code_challenge == "client_challenge_value"
        )  # Client's challenge
        assert transaction.proxy_code_verifier is not None  # Proxy generated its own
        # Proxy code challenge is computed from verifier when needed
        assert len(transaction.proxy_code_verifier) > 0
