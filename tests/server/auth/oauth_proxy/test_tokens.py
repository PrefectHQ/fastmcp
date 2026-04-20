"""Tests for OAuth proxy token endpoint and handling."""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.handlers.token import TokenErrorResponse
from mcp.server.auth.handlers.token import TokenHandler as SDKTokenHandler
from mcp.server.auth.provider import AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.auth import (
    AccessToken,
    RefreshToken,
    TokenHandler,
    TokenVerifier,
)
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import (
    DEFAULT_ACCESS_TOKEN_EXPIRY_NO_REFRESH_SECONDS,
    DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
    DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS,
    ClientCode,
    JTIMapping,
    RefreshTokenMetadata,
    UpstreamTokenSet,
    _hash_token,
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
            client_storage=MemoryStore(),
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
            client_storage=MemoryStore(),
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
            client_storage=MemoryStore(),
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
            client_storage=MemoryStore(),
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
            client_storage=MemoryStore(),
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
            client_storage=MemoryStore(),
        )

        assert provider._fallback_access_token_expiry_seconds is None


class TestFallbackRefreshTokenExpiry:
    """Tests for fallback_refresh_token_expiry_seconds (issue #3987).

    When upstream omits `refresh_expires_in`, the FastMCP refresh JWT and the
    JTI mapping must use the configured fallback (default 1 year), not a
    hardcoded 30-day value. The FastMCP refresh JWT is a signed pointer; the
    real upstream refresh remains the source of truth.
    """

    @pytest.fixture
    def jwt_verifier(self):
        verifier = Mock(spec=TokenVerifier)
        verifier.required_scopes = ["read", "write"]
        verifier.verify_token = AsyncMock(return_value=None)
        return verifier

    def test_default_constant(self):
        assert DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS == 60 * 60 * 24 * 365  # 1 year

    def test_fallback_defaults_to_one_year(self, jwt_verifier):
        provider = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="http://localhost:8000",
            jwt_signing_key="test-signing-key",
            client_storage=MemoryStore(),
        )
        assert (
            provider._fallback_refresh_token_expiry_seconds
            == DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS
        )

    def test_fallback_parameter_stored(self, jwt_verifier):
        provider = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="http://localhost:8000",
            jwt_signing_key="test-signing-key",
            fallback_refresh_token_expiry_seconds=7 * 24 * 60 * 60,  # 7 days
            client_storage=MemoryStore(),
        )
        assert provider._fallback_refresh_token_expiry_seconds == 7 * 24 * 60 * 60

    async def test_initial_exchange_jti_mapping_aligned_with_refresh_expiry(
        self, jwt_verifier
    ):
        """Bug 1: JTI mapping TTL must align with refresh_expires_in.

        Previously hardcoded to 30 days, which silently expired the mapping
        early when upstream returned a longer refresh lifetime.
        """
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret-key",
            client_storage=MemoryStore(),
        )
        proxy.set_mcp_path("/mcp")

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client)

        # Upstream returns 1-year refresh expiry — well past the old 30d default
        one_year = 60 * 60 * 24 * 365
        client_code = ClientCode(
            code="long-refresh-code",
            client_id="test-client",
            redirect_uri="http://localhost:12345/callback",
            code_challenge="test-challenge",
            code_challenge_method="S256",
            scopes=["read", "write"],
            idp_tokens={
                "access_token": "upstream-access",
                "refresh_token": "upstream-refresh",
                "expires_in": 3600,
                "refresh_expires_in": one_year,
                "token_type": "Bearer",
            },
            expires_at=time.time() + 300,
            created_at=time.time(),
        )
        await proxy._code_store.put(key=client_code.code, value=client_code)

        result = await proxy.exchange_authorization_code(
            client=client,
            authorization_code=AuthorizationCode(
                code="long-refresh-code",
                scopes=["read", "write"],
                expires_at=time.time() + 300,
                client_id="test-client",
                code_challenge="test-challenge",
                redirect_uri=AnyUrl("http://localhost:12345/callback"),
                redirect_uri_provided_explicitly=True,
            ),
        )
        assert result.refresh_token is not None

        # The refresh JWT exp claim should reflect the 1-year upstream lifetime
        refresh_payload = proxy.jwt_issuer.verify_token(
            result.refresh_token, expected_token_use="refresh"
        )
        # exp - iat should be approximately one_year (allow small clock skew)
        assert refresh_payload["exp"] - refresh_payload["iat"] == pytest.approx(
            one_year, abs=5
        )

    async def test_initial_exchange_uses_fallback_when_upstream_silent(
        self, jwt_verifier
    ):
        """When upstream omits refresh_expires_in, fall back to configured value.

        Default is 1 year (was previously 30 days, ignoring user config).
        """
        custom_fallback = 60 * 60 * 24 * 90  # 90 days
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret-key",
            fallback_refresh_token_expiry_seconds=custom_fallback,
            client_storage=MemoryStore(),
        )
        proxy.set_mcp_path("/mcp")

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client)

        client_code = ClientCode(
            code="silent-refresh-code",
            client_id="test-client",
            redirect_uri="http://localhost:12345/callback",
            code_challenge="test-challenge",
            code_challenge_method="S256",
            scopes=["read", "write"],
            idp_tokens={
                "access_token": "upstream-access",
                "refresh_token": "upstream-refresh",
                "expires_in": 3600,
                # no refresh_expires_in — upstream is silent
                "token_type": "Bearer",
            },
            expires_at=time.time() + 300,
            created_at=time.time(),
        )
        await proxy._code_store.put(key=client_code.code, value=client_code)

        result = await proxy.exchange_authorization_code(
            client=client,
            authorization_code=AuthorizationCode(
                code="silent-refresh-code",
                scopes=["read", "write"],
                expires_at=time.time() + 300,
                client_id="test-client",
                code_challenge="test-challenge",
                redirect_uri=AnyUrl("http://localhost:12345/callback"),
                redirect_uri_provided_explicitly=True,
            ),
        )
        assert result.refresh_token is not None

        refresh_payload = proxy.jwt_issuer.verify_token(
            result.refresh_token, expected_token_use="refresh"
        )
        assert refresh_payload["exp"] - refresh_payload["iat"] == pytest.approx(
            custom_fallback, abs=5
        )

    async def test_refresh_jwt_exp_aligned_when_upstream_omits_refresh_token(
        self, jwt_verifier
    ):
        """Bug 2: JWT exp must align with storage TTL when upstream omits refresh_token.

        Cognito does NOT return a refresh_token on refresh. Previously the new
        FastMCP refresh JWT was issued with hardcoded 30-day exp, while the
        storage TTL correctly used the existing upstream refresh lifetime —
        forcing re-login at day 30 even when the upstream refresh was valid
        for much longer.
        """
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret-key",
            client_storage=MemoryStore(),
        )
        proxy.set_mcp_path("/mcp")

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client)

        # Seed an upstream session whose refresh token has 6 months left
        now = time.time()
        upstream_refresh_expires_at = now + (60 * 60 * 24 * 180)
        upstream_token_id = "upstream-id-cognito"
        upstream_token_set = UpstreamTokenSet(
            upstream_token_id=upstream_token_id,
            access_token="upstream-access-old",
            refresh_token="upstream-refresh-tok",
            refresh_token_expires_at=upstream_refresh_expires_at,
            expires_at=now + 60,  # access nearly expired (irrelevant here)
            token_type="Bearer",
            scope="read write",
            client_id="test-client",
            created_at=now,
            raw_token_data={},
        )
        await proxy._upstream_token_store.put(
            key=upstream_token_id,
            value=upstream_token_set,
            ttl=int(upstream_refresh_expires_at - now),
        )

        # Issue a FastMCP refresh JWT pointing at this session
        old_refresh_jti = "old-refresh-jti"
        old_refresh_jwt = proxy.jwt_issuer.issue_refresh_token(
            client_id="test-client",
            scopes=["read", "write"],
            jti=old_refresh_jti,
            expires_in=int(upstream_refresh_expires_at - now),
        )
        await proxy._jti_mapping_store.put(
            key=old_refresh_jti,
            value=JTIMapping(
                jti=old_refresh_jti,
                upstream_token_id=upstream_token_id,
                created_at=now,
            ),
            ttl=int(upstream_refresh_expires_at - now),
        )
        await proxy._refresh_token_store.put(
            key=_hash_token(old_refresh_jwt),
            value=RefreshTokenMetadata(
                client_id="test-client",
                scopes=["read", "write"],
                expires_at=int(upstream_refresh_expires_at),
                created_at=now,
            ),
            ttl=int(upstream_refresh_expires_at - now),
        )

        # Mock the upstream refresh response — Cognito-style: NO refresh_token
        async def fake_refresh(url, refresh_token, scope=None, **kwargs):
            return {
                "access_token": "upstream-access-new",
                "expires_in": 3600,
                "token_type": "Bearer",
                # NO refresh_token, NO refresh_expires_in
            }

        oauth_client_mock = Mock()
        oauth_client_mock.refresh_token = AsyncMock(side_effect=fake_refresh)
        with patch.object(
            proxy,
            "_create_upstream_oauth_client",
            return_value=oauth_client_mock,
        ):
            new_token = await proxy.exchange_refresh_token(
                client=client,
                refresh_token=RefreshToken(
                    token=old_refresh_jwt,
                    client_id="test-client",
                    scopes=["read", "write"],
                    expires_at=int(upstream_refresh_expires_at),
                ),
                scopes=["read", "write"],
            )

        assert new_token.refresh_token is not None
        # New refresh JWT exp must reflect remaining upstream refresh lifetime
        # (~6 months), not a hardcoded 30 days
        new_refresh_payload = proxy.jwt_issuer.verify_token(
            new_token.refresh_token, expected_token_use="refresh"
        )
        ttl_remaining = new_refresh_payload["exp"] - new_refresh_payload["iat"]
        # Should be close to 180 days, well above the old 30-day cliff
        assert ttl_remaining > 60 * 60 * 24 * 90  # at least 90 days


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
            client_storage=MemoryStore(),
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
        refresh_payload = proxy.jwt_issuer.verify_token(
            result.refresh_token, expected_token_use="refresh"
        )
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
        refresh_payload = proxy.jwt_issuer.verify_token(
            result.refresh_token, expected_token_use="refresh"
        )
        refresh_jti = refresh_payload["jti"]

        jti_mapping = await proxy._jti_mapping_store.get(key=refresh_jti)
        assert jti_mapping is not None

        upstream_tokens = await proxy._upstream_token_store.get(
            key=jti_mapping.upstream_token_id
        )
        assert upstream_tokens is not None

    async def test_refresh_expires_in_zero_issues_refresh_token(self, proxy):
        """refresh_expires_in=0 should fall back to the configured default.

        Keycloak returns refresh_expires_in=0 for offline tokens (offline_access scope),
        meaning "no fixed time-based expiry". The proxy should still issue a PROXY_RT.
        """
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client)

        client_code = ClientCode(
            code="test-auth-code-keycloak-offline",
            client_id="test-client",
            redirect_uri="http://localhost:12345/callback",
            code_challenge="test-challenge",
            code_challenge_method="S256",
            scopes=["read", "write"],
            idp_tokens={
                "access_token": "upstream-access-token-kc",
                "refresh_token": "upstream-refresh-token-kc",
                "expires_in": 3600,
                "refresh_expires_in": 0,  # Keycloak offline token convention
                "token_type": "Bearer",
            },
            expires_at=time.time() + 300,
            created_at=time.time(),
        )
        await proxy._code_store.put(key=client_code.code, value=client_code)

        auth_code = AuthorizationCode(
            code="test-auth-code-keycloak-offline",
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

        # refresh_expires_in=0 must NOT prevent refresh token issuance
        assert result.access_token is not None
        assert result.refresh_token is not None

        # Verify refresh token metadata was stored
        refresh_meta = await proxy._refresh_token_store.get(
            key=_hash_token(result.refresh_token)
        )
        assert refresh_meta is not None


class TestTransparentUpstreamRefresh:
    """Tests for transparent upstream token refresh in load_access_token.

    When the upstream token expires but a refresh token is available, the proxy
    should transparently refresh the upstream token rather than returning a 401
    that forces the client into a full re-authentication flow.
    """

    @pytest.fixture
    def mock_verifier(self):
        """Token verifier that rejects expired tokens, accepts refreshed ones."""
        verifier = Mock(spec=TokenVerifier)
        verifier.required_scopes = ["read"]

        # Cache tokens to test mutation of returned objects
        cache: dict[str, AccessToken] = {}

        async def verify(token: str) -> AccessToken | None:
            if token in cache:
                return cache[token]

            if token.startswith("refreshed-") or token.startswith("valid-"):
                t = AccessToken(
                    token=token,
                    client_id="test-client",
                    scopes=["read"],
                    expires_at=int(time.time() + 3600),
                )
                cache[token] = t
                return t
            return None

        verifier.verify_token = AsyncMock(side_effect=verify)
        return verifier

    @pytest.fixture
    def proxy(self, mock_verifier):
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=mock_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret-key",
            client_storage=MemoryStore(),
        )
        proxy.set_mcp_path("/mcp")
        return proxy

    async def _setup_expired_session(
        self,
        proxy: OAuthProxy,
        *,
        upstream_refresh_token: str | None = "upstream-refresh-tok",
    ) -> str:
        """Set up a proxy JWT pointing at an expired upstream token.

        Returns the proxy JWT (access token) that can be passed to
        load_access_token.
        """
        upstream_token_id = "upstream-tok-id"
        access_jti = "test-access-jti"

        upstream_token_set = UpstreamTokenSet(
            upstream_token_id=upstream_token_id,
            access_token="expired-upstream-access",
            refresh_token=upstream_refresh_token,
            refresh_token_expires_at=time.time() + 86400
            if upstream_refresh_token
            else None,
            expires_at=time.time() - 60,  # expired 1 minute ago
            token_type="Bearer",
            scope="read",
            client_id="test-client",
            created_at=time.time() - 3600,
        )
        await proxy._upstream_token_store.put(
            key=upstream_token_id,
            value=upstream_token_set,
            ttl=86400,
        )

        await proxy._jti_mapping_store.put(
            key=access_jti,
            value=JTIMapping(
                jti=access_jti,
                upstream_token_id=upstream_token_id,
                created_at=time.time(),
            ),
            ttl=3600,
        )

        fastmcp_jwt = proxy.jwt_issuer.issue_access_token(
            client_id="test-client",
            scopes=["read"],
            jti=access_jti,
            expires_in=3600,
        )
        return fastmcp_jwt

    async def _setup_session_with_claims(self, proxy, *, upstream_claims=None):
        """Set up a proxy JWT pointing at a valid upstream token, with optional upstream_claims."""
        upstream_token_id = "upstream-tok-id"
        access_jti = "test-claims-jti"

        upstream_token_set = UpstreamTokenSet(
            upstream_token_id=upstream_token_id,
            access_token="valid-upstream-access",
            refresh_token=None,
            refresh_token_expires_at=None,
            expires_at=time.time() + 3600,
            token_type="Bearer",
            scope="read",
            client_id="test-client",
            created_at=time.time(),
        )
        await proxy._upstream_token_store.put(
            key=upstream_token_id,
            value=upstream_token_set,
            ttl=3600,
        )
        await proxy._jti_mapping_store.put(
            key=access_jti,
            value=JTIMapping(
                jti=access_jti,
                upstream_token_id=upstream_token_id,
                created_at=time.time(),
            ),
            ttl=3600,
        )
        return proxy.jwt_issuer.issue_access_token(
            client_id="test-client",
            scopes=["read"],
            jti=access_jti,
            expires_in=3600,
            upstream_claims=upstream_claims,
        )

    async def test_transparent_refresh_on_expired_upstream(self, proxy):
        """load_access_token refreshes upstream token when validation fails."""
        fastmcp_jwt = await self._setup_expired_session(proxy)

        mock_oauth_client = AsyncMock()
        mock_oauth_client.refresh_token = AsyncMock(
            return_value={
                "access_token": "refreshed-upstream-access",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "upstream-refresh-tok",
                "scope": "read",
            }
        )

        with patch.object(
            proxy, "_create_upstream_oauth_client", return_value=mock_oauth_client
        ):
            result = await proxy.load_access_token(fastmcp_jwt)

        assert result is not None
        assert result.token == "refreshed-upstream-access"
        mock_oauth_client.refresh_token.assert_called_once()

    async def test_transparent_refresh_updates_stored_token(self, proxy):
        """After transparent refresh, the stored upstream token is updated."""
        fastmcp_jwt = await self._setup_expired_session(proxy)

        mock_oauth_client = AsyncMock()
        mock_oauth_client.refresh_token = AsyncMock(
            return_value={
                "access_token": "refreshed-upstream-access",
                "token_type": "Bearer",
                "expires_in": 7200,
                "refresh_token": "upstream-refresh-tok",
                "scope": "read",
            }
        )

        with patch.object(
            proxy, "_create_upstream_oauth_client", return_value=mock_oauth_client
        ):
            await proxy.load_access_token(fastmcp_jwt)

        stored = await proxy._upstream_token_store.get(key="upstream-tok-id")
        assert stored is not None
        assert stored.access_token == "refreshed-upstream-access"
        assert stored.expires_at > time.time()

    async def test_no_refresh_without_refresh_token(self, proxy):
        """Without a refresh token, load_access_token returns None immediately."""
        fastmcp_jwt = await self._setup_expired_session(
            proxy, upstream_refresh_token=None
        )

        result = await proxy.load_access_token(fastmcp_jwt)

        assert result is None

    async def test_returns_none_when_refresh_fails(self, proxy):
        """If the upstream refresh call fails, load_access_token returns None."""
        fastmcp_jwt = await self._setup_expired_session(proxy)

        mock_oauth_client = AsyncMock()
        mock_oauth_client.refresh_token = AsyncMock(
            side_effect=Exception("upstream refused refresh")
        )

        with patch.object(
            proxy, "_create_upstream_oauth_client", return_value=mock_oauth_client
        ):
            result = await proxy.load_access_token(fastmcp_jwt)

        assert result is None

    async def test_returns_none_when_refreshed_token_still_invalid(self, proxy):
        """If the refreshed token also fails validation, returns None."""
        fastmcp_jwt = await self._setup_expired_session(proxy)

        mock_oauth_client = AsyncMock()
        mock_oauth_client.refresh_token = AsyncMock(
            return_value={
                "access_token": "still-bad-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "read",
            }
        )

        with patch.object(
            proxy, "_create_upstream_oauth_client", return_value=mock_oauth_client
        ):
            result = await proxy.load_access_token(fastmcp_jwt)

        # "still-bad-token" doesn't start with "refreshed-" so verifier rejects it
        assert result is None

    async def test_no_refresh_when_token_not_expired(self, proxy):
        """Non-expiry validation failures (e.g. revocation) should not trigger refresh."""
        upstream_token_id = "upstream-tok-id"
        access_jti = "test-access-jti"

        # Token is NOT expired — verification failure is for another reason
        upstream_token_set = UpstreamTokenSet(
            upstream_token_id=upstream_token_id,
            access_token="revoked-upstream-access",
            refresh_token="upstream-refresh-tok",
            refresh_token_expires_at=time.time() + 86400,
            expires_at=time.time() + 3600,  # still valid for 1 hour
            token_type="Bearer",
            scope="read",
            client_id="test-client",
            created_at=time.time() - 60,
        )
        await proxy._upstream_token_store.put(
            key=upstream_token_id,
            value=upstream_token_set,
            ttl=86400,
        )
        await proxy._jti_mapping_store.put(
            key=access_jti,
            value=JTIMapping(
                jti=access_jti,
                upstream_token_id=upstream_token_id,
                created_at=time.time(),
            ),
            ttl=3600,
        )
        fastmcp_jwt = proxy.jwt_issuer.issue_access_token(
            client_id="test-client",
            scopes=["read"],
            jti=access_jti,
            expires_in=3600,
        )

        mock_oauth_client = AsyncMock()
        mock_oauth_client.refresh_token = AsyncMock()

        with patch.object(
            proxy, "_create_upstream_oauth_client", return_value=mock_oauth_client
        ):
            result = await proxy.load_access_token(fastmcp_jwt)

        assert result is None
        # Refresh should NOT have been attempted
        mock_oauth_client.refresh_token.assert_not_called()

    async def test_reload_from_storage_after_refresh_failure(self, proxy):
        """If refresh fails, re-read from storage in case another worker refreshed."""
        fastmcp_jwt = await self._setup_expired_session(proxy)

        # Simulate: refresh fails (stale refresh token), but another worker
        # already wrote a fresh upstream token to storage.
        refreshed_token_set = UpstreamTokenSet(
            upstream_token_id="upstream-tok-id",
            access_token="refreshed-upstream-access",
            refresh_token="new-refresh-tok",
            refresh_token_expires_at=time.time() + 86400,
            expires_at=time.time() + 3600,
            token_type="Bearer",
            scope="read",
            client_id="test-client",
            created_at=time.time(),
        )

        # The store is read three times:
        # 1. Initial lookup in load_access_token
        # 2. Re-read inside the advisory lock
        # 3. Re-read after refresh failure (recovery path)
        original_get = proxy._upstream_token_store.get
        call_count = 0

        async def mock_get(key: str) -> UpstreamTokenSet | None:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return await original_get(key)
            # After refresh failure, return the "other worker's" refreshed token
            return refreshed_token_set

        mock_oauth_client = AsyncMock()
        mock_oauth_client.refresh_token = AsyncMock(
            side_effect=Exception("refresh token rotated by another worker")
        )

        with (
            patch.object(
                proxy, "_create_upstream_oauth_client", return_value=mock_oauth_client
            ),
            patch.object(proxy._upstream_token_store, "get", side_effect=mock_get),
        ):
            result = await proxy.load_access_token(fastmcp_jwt)

        assert result is not None
        assert result.token == "refreshed-upstream-access"

    def test_refresh_lock_cache_bounded(self, proxy, monkeypatch):
        """_get_refresh_lock never grows beyond _REFRESH_LOCK_CACHE_SIZE."""
        monkeypatch.setattr(
            "fastmcp.server.auth.oauth_proxy.proxy._REFRESH_LOCK_CACHE_SIZE", 3
        )
        for i in range(10):
            proxy._get_refresh_lock(f"token-{i}")
        assert len(proxy._refresh_locks) == 3

    def test_refresh_lock_lru_evicts_least_recently_used(self, proxy, monkeypatch):
        """Touching an entry promotes it; eviction removes the oldest untouched entry."""
        monkeypatch.setattr(
            "fastmcp.server.auth.oauth_proxy.proxy._REFRESH_LOCK_CACHE_SIZE", 3
        )
        proxy._get_refresh_lock("a")
        proxy._get_refresh_lock("b")
        proxy._get_refresh_lock("c")
        # Touch "a" to move it to MRU position
        proxy._get_refresh_lock("a")
        # Adding "d" should evict "b" (oldest untouched)
        proxy._get_refresh_lock("d")
        assert "b" not in proxy._refresh_locks
        assert "a" in proxy._refresh_locks
        assert "c" in proxy._refresh_locks
        assert "d" in proxy._refresh_locks

    def test_refresh_lock_same_token_returns_same_lock(self, proxy):
        """Requesting the same token ID twice returns the same lock object."""
        lock1 = proxy._get_refresh_lock("tok")
        lock2 = proxy._get_refresh_lock("tok")
        assert lock1 is lock2

    async def test_upstream_claims_propagated(self, proxy):
        jwt = await self._setup_session_with_claims(
            proxy, upstream_claims={"sub": "user-123"}
        )
        result = await proxy.load_access_token(jwt)
        assert result is not None
        assert result.claims["upstream_claims"] == {"sub": "user-123"}

    async def test_upstream_claims_not_mutated_on_cached_token(
        self, proxy, mock_verifier
    ):
        jwt = await self._setup_session_with_claims(
            proxy, upstream_claims={"sub": "user-123"}
        )
        result = await proxy.load_access_token(jwt)
        assert result is not None
        assert result.claims["upstream_claims"] == {"sub": "user-123"}
        # Original verifier result must not be mutated
        for call in list(mock_verifier.verify_token.call_args_list):
            returned = await mock_verifier.verify_token(call.args[0])
            if returned:
                assert "upstream_claims" not in returned.claims
