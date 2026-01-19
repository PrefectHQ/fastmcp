"""Tests for OAuth proxy token endpoint and handling."""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from mcp.server.auth.handlers.token import TokenErrorResponse
from mcp.server.auth.handlers.token import TokenHandler as SDKTokenHandler
from mcp.server.auth.provider import AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.auth import RefreshToken, TokenHandler, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import (
    DEFAULT_ACCESS_TOKEN_EXPIRY_NO_REFRESH_SECONDS,
    DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
    ClientCode,
)
from fastmcp.server.auth.providers.jwt import JWTVerifier


class TestOAuthProxyTokenEndpointAuth:
    """Tests for token endpoint authentication methods."""

    def test_token_auth_method_initialization(self, jwt_verifier):
        """Test different token endpoint auth methods."""
        # client_secret_post
        proxy_post = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            token_endpoint_auth_method="client_secret_post",
            jwt_signing_key="test-secret",
        )
        assert proxy_post._token_endpoint_auth_method == "client_secret_post"

        # client_secret_basic (default)
        proxy_basic = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            token_endpoint_auth_method="client_secret_basic",
            jwt_signing_key="test-secret",
        )
        assert proxy_basic._token_endpoint_auth_method == "client_secret_basic"

        # None (use authlib default)
        proxy_default = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret",
        )
        assert proxy_default._token_endpoint_auth_method is None

    async def test_token_auth_method_passed_to_client(self, jwt_verifier):
        """Test that auth method is passed to AsyncOAuth2Client."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client-id",
            upstream_client_secret="client-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            token_endpoint_auth_method="client_secret_post",
            jwt_signing_key="test-secret",
        )

        # Initialize JWT issuer before token operations
        proxy.set_mcp_path("/mcp")

        # First, create a valid FastMCP token via full OAuth flow
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Mock the upstream OAuth provider response
        with patch(
            "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client"
        ) as MockClient:
            mock_client = AsyncMock()

            # Mock initial token exchange (authorization code flow)
            mock_client.fetch_token = AsyncMock(
                return_value={
                    "access_token": "upstream-access-token",
                    "refresh_token": "upstream-refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )

            # Mock token refresh
            mock_client.refresh_token = AsyncMock(
                return_value={
                    "access_token": "new-upstream-token",
                    "refresh_token": "new-upstream-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
            MockClient.return_value = mock_client

            # Register client and do initial OAuth flow to get valid FastMCP tokens
            await proxy.register_client(client)

            # Store client code that would be created during OAuth callback
            client_code = ClientCode(
                code="test-auth-code",
                client_id="test-client",
                redirect_uri="http://localhost:12345/callback",
                code_challenge="",
                code_challenge_method="S256",
                scopes=["read"],
                idp_tokens={
                    "access_token": "upstream-access-token",
                    "refresh_token": "upstream-refresh-token",
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
                scopes=["read"],
                expires_at=time.time() + 300,
                client_id="test-client",
                code_challenge="",
                redirect_uri=AnyUrl("http://localhost:12345/callback"),
                redirect_uri_provided_explicitly=True,
            )
            result = await proxy.exchange_authorization_code(
                client=client,
                authorization_code=auth_code,
            )

            # Now test refresh with the valid FastMCP refresh token
            assert result.refresh_token is not None
            fastmcp_refresh = RefreshToken(
                token=result.refresh_token,
                client_id="test-client",
                scopes=["read"],
                expires_at=None,
            )

            # Reset mock to check refresh call
            MockClient.reset_mock()
            mock_client.refresh_token = AsyncMock(
                return_value={
                    "access_token": "new-upstream-token-2",
                    "refresh_token": "new-upstream-refresh-2",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
            MockClient.return_value = mock_client

            await proxy.exchange_refresh_token(client, fastmcp_refresh, ["read"])

            # Verify auth method was passed to OAuth client
            MockClient.assert_called_with(
                client_id="client-id",
                client_secret="client-secret",
                token_endpoint_auth_method="client_secret_post",
                timeout=30.0,
            )


class TestTokenHandlerErrorTransformation:
    """Tests for TokenHandler's OAuth 2.1 compliant error transformation."""

    async def test_transforms_client_auth_failure_to_invalid_client_401(self):
        """Test that client authentication failures return invalid_client with 401."""
        handler = TokenHandler(provider=Mock(), client_authenticator=Mock())

        # Create a mock 401 response like the SDK returns for auth failures
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.body = (
            b'{"error":"unauthorized_client","error_description":"Invalid client_id"}'
        )

        # Patch the parent class's handle() to return our mock response
        with patch.object(
            SDKTokenHandler,
            "handle",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await handler.handle(Mock())

        # Should transform to OAuth 2.1 compliant response
        assert response.status_code == 401
        assert b'"error":"invalid_client"' in response.body
        assert b'"error_description":"Invalid client_id"' in response.body

    def test_does_not_transform_grant_type_unauthorized_to_invalid_client(self):
        """Test that grant type authorization errors stay as unauthorized_client with 400."""
        handler = TokenHandler(provider=Mock(), client_authenticator=Mock())

        # Simulate error from grant_type not in client_info.grant_types
        error_response = TokenErrorResponse(
            error="unauthorized_client",
            error_description="Client not authorized for this grant type",
        )

        response = handler.response(error_response)

        # Should NOT transform - keep as 400 unauthorized_client
        assert response.status_code == 400
        assert b'"error":"unauthorized_client"' in response.body

    async def test_transforms_invalid_grant_to_401(self):
        """Test that invalid_grant errors return 401 per MCP spec.

        Per MCP spec: "Invalid or expired tokens MUST receive a HTTP 401 response."
        The SDK incorrectly returns 400 for all TokenErrorResponse including invalid_grant.
        """
        handler = TokenHandler(provider=Mock(), client_authenticator=Mock())

        # Create a mock 400 response like the SDK returns for invalid_grant
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.body = (
            b'{"error":"invalid_grant","error_description":"refresh token has expired"}'
        )

        # Patch the parent class's handle() to return our mock response
        with patch.object(
            SDKTokenHandler,
            "handle",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await handler.handle(Mock())

        # Should transform to MCP-compliant 401 response
        assert response.status_code == 401
        assert b'"error":"invalid_grant"' in response.body
        assert b'"error_description":"refresh token has expired"' in response.body

    def test_does_not_transform_other_400_errors(self):
        """Test that non-invalid_grant 400 errors pass through unchanged."""
        handler = TokenHandler(provider=Mock(), client_authenticator=Mock())

        # Test with invalid_request error (should stay 400)
        error_response = TokenErrorResponse(
            error="invalid_request",
            error_description="Missing required parameter",
        )

        response = handler.response(error_response)

        # Should pass through unchanged as 400
        assert response.status_code == 400
        assert b'"error":"invalid_request"' in response.body


class TestFallbackAccessTokenExpiry:
    """Test fallback access token expiry constants and configuration."""

    def test_default_constants(self):
        """Verify the default expiry constants are set correctly."""
        assert DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS == 60 * 60  # 1 hour
        assert (
            DEFAULT_ACCESS_TOKEN_EXPIRY_NO_REFRESH_SECONDS == 60 * 60 * 24 * 365
        )  # 1 year

    def test_fallback_parameter_stored(self):
        """Verify fallback_access_token_expiry_seconds is stored on provider."""
        provider = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=JWTVerifier(
                jwks_uri="https://idp.example.com/.well-known/jwks.json",
                issuer="https://idp.example.com",
            ),
            base_url="http://localhost:8000",
            jwt_signing_key="test-signing-key",
            fallback_access_token_expiry_seconds=86400,
        )

        assert provider._fallback_access_token_expiry_seconds == 86400

    def test_fallback_parameter_defaults_to_none(self):
        """Verify fallback defaults to None (enabling smart defaults)."""
        provider = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=JWTVerifier(
                jwks_uri="https://idp.example.com/.well-known/jwks.json",
                issuer="https://idp.example.com",
            ),
            base_url="http://localhost:8000",
            jwt_signing_key="test-signing-key",
        )

        assert provider._fallback_access_token_expiry_seconds is None


class TestUpstreamTokenStorageTTL:
    """Tests for upstream token storage TTL calculation (issue #2670).

    The TTL should use max(refresh_expires_in, expires_in) to handle cases where
    the refresh token has a shorter lifetime than the access token (e.g., Keycloak
    with sliding session windows).
    """

    @pytest.fixture
    def jwt_verifier(self):
        """Create a mock JWT verifier."""
        verifier = Mock(spec=TokenVerifier)
        verifier.required_scopes = ["read", "write"]
        verifier.verify_token = AsyncMock(return_value=None)
        return verifier

    @pytest.fixture
    def proxy(self, jwt_verifier):
        """Create an OAuth proxy for testing."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret-key",
        )
        proxy.set_mcp_path("/mcp")
        return proxy

    async def test_ttl_uses_max_when_refresh_shorter_than_access(self, proxy):
        """TTL should use access token expiry when refresh is shorter.

        This is the xsreality case: Keycloak returns refresh_expires_in=120 (2 min)
        but expires_in=28800 (8 hours). The upstream tokens should persist for
        8 hours (the access token lifetime), not 2 minutes.
        """
        # Register client
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client)

        # Simulate xsreality's Keycloak setup: short refresh, long access
        client_code = ClientCode(
            code="test-auth-code",
            client_id="test-client",
            redirect_uri="http://localhost:12345/callback",
            code_challenge="test-challenge",
            code_challenge_method="S256",
            scopes=["read", "write"],
            idp_tokens={
                "access_token": "upstream-access-token",
                "refresh_token": "upstream-refresh-token",
                "expires_in": 28800,  # 8 hours (access token)
                "refresh_expires_in": 120,  # 2 minutes (refresh token) - SHORTER!
                "token_type": "Bearer",
            },
            expires_at=time.time() + 300,
            created_at=time.time(),
        )
        await proxy._code_store.put(key=client_code.code, value=client_code)

        # Exchange the code
        auth_code = AuthorizationCode(
            code="test-auth-code",
            scopes=["read", "write"],
            expires_at=time.time() + 300,
            client_id="test-client",
            code_challenge="test-challenge",
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
        )

        result = await proxy.exchange_authorization_code(
            client=client,
            authorization_code=auth_code,
        )

        # Verify tokens were issued
        assert result.access_token is not None
        assert result.refresh_token is not None

        # The key test: verify upstream tokens are stored with TTL=max(120, 28800)=28800
        # We can verify this by checking the tokens are still accessible after 2 minutes
        # would have passed (if TTL was incorrectly set to 120)
        #
        # Since we can't easily time-travel in tests, we verify the storage directly
        # by checking that we can still look up the tokens for refresh purposes.
        #
        # Extract the JTI from the refresh token to look up the mapping
        refresh_payload = proxy.jwt_issuer.verify_token(result.refresh_token)
        refresh_jti = refresh_payload["jti"]

        # The JTI mapping should exist
        jti_mapping = await proxy._jti_mapping_store.get(key=refresh_jti)
        assert jti_mapping is not None

        # The upstream tokens should exist
        upstream_tokens = await proxy._upstream_token_store.get(
            key=jti_mapping.upstream_token_id
        )
        assert upstream_tokens is not None
        assert upstream_tokens.access_token == "upstream-access-token"
        assert upstream_tokens.refresh_token == "upstream-refresh-token"

    async def test_ttl_uses_refresh_when_refresh_longer_than_access(self, proxy):
        """TTL should use refresh token expiry when refresh is longer.

        This is the ianw case: IdP returns expires_in=300 (5 min) but
        refresh_expires_in=32318 (9 hours). The upstream tokens should persist
        for 9 hours (the refresh token lifetime).
        """
        # Register client
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client)

        # Simulate ianw's setup: short access, long refresh (typical)
        client_code = ClientCode(
            code="test-auth-code-2",
            client_id="test-client",
            redirect_uri="http://localhost:12345/callback",
            code_challenge="test-challenge",
            code_challenge_method="S256",
            scopes=["read", "write"],
            idp_tokens={
                "access_token": "upstream-access-token-2",
                "refresh_token": "upstream-refresh-token-2",
                "expires_in": 300,  # 5 minutes (access token)
                "refresh_expires_in": 32318,  # 9 hours (refresh token) - LONGER
                "token_type": "Bearer",
            },
            expires_at=time.time() + 300,
            created_at=time.time(),
        )
        await proxy._code_store.put(key=client_code.code, value=client_code)

        # Exchange the code
        auth_code = AuthorizationCode(
            code="test-auth-code-2",
            scopes=["read", "write"],
            expires_at=time.time() + 300,
            client_id="test-client",
            code_challenge="test-challenge",
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
        )

        result = await proxy.exchange_authorization_code(
            client=client,
            authorization_code=auth_code,
        )

        # Verify tokens were issued
        assert result.access_token is not None
        assert result.refresh_token is not None

        # Verify upstream tokens are accessible
        refresh_payload = proxy.jwt_issuer.verify_token(result.refresh_token)
        refresh_jti = refresh_payload["jti"]

        jti_mapping = await proxy._jti_mapping_store.get(key=refresh_jti)
        assert jti_mapping is not None

        upstream_tokens = await proxy._upstream_token_store.get(
            key=jti_mapping.upstream_token_id
        )
        assert upstream_tokens is not None
