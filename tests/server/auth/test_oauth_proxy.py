"""Comprehensive tests for OAuth Proxy Provider functionality."""

import time
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.auth import AccessToken, RefreshToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier


class TestOAuthProxyComprehensive:
    """Comprehensive test suite for OAuthProxy provider functionality."""

    @pytest.fixture
    def jwt_verifier(self):
        """Create a mock JWT verifier for testing."""
        verifier = Mock(spec=JWTVerifier)
        verifier.required_scopes = ["read", "write"]
        verifier.verify_token = Mock(return_value=None)
        return verifier

    @pytest.fixture
    def oauth_proxy(self, jwt_verifier):
        """Create an OAuthProxy instance for testing."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            redirect_path="/auth/callback",
        )

    def test_initialization_with_string_urls(self, jwt_verifier):
        """Test OAuthProxy initialization with string URLs (not AnyHttpUrl objects)."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",  # String instead of AnyHttpUrl
            issuer_url="https://issuer.example.com",  # String
            service_documentation_url="https://docs.example.com",  # String
        )

        # Should work fine and convert internally to AnyHttpUrl
        assert str(proxy.base_url) == "https://api.example.com/"
        assert str(proxy.issuer_url) == "https://issuer.example.com/"
        assert str(proxy.service_documentation_url) == "https://docs.example.com/"
        assert (
            proxy.client_registration_options is not None
            and proxy.client_registration_options.valid_scopes == ["read", "write"]
        )

    def test_initialization_with_all_parameters(self, jwt_verifier):
        """Test OAuthProxy initialization with all optional parameters."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            upstream_revocation_endpoint="https://auth.example.com/revoke",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            redirect_path="/auth/callback",
            issuer_url="https://issuer.example.com",
            service_documentation_url="https://docs.example.com",
            valid_scopes=["open", "close"],
        )

        # Verify all parameters are set correctly
        assert (
            proxy._upstream_authorization_endpoint
            == "https://auth.example.com/authorize"
        )
        assert proxy._upstream_token_endpoint == "https://auth.example.com/token"
        assert proxy._upstream_client_id == "client-123"
        assert proxy._upstream_client_secret.get_secret_value() == "secret-456"
        assert proxy._upstream_revocation_endpoint == "https://auth.example.com/revoke"
        assert proxy._redirect_path == "/auth/callback"
        assert str(proxy.issuer_url) == "https://issuer.example.com/"
        assert str(proxy.service_documentation_url) == "https://docs.example.com/"
        assert (
            proxy.client_registration_options is not None
            and proxy.client_registration_options.valid_scopes == ["open", "close"]
        )

    def test_redirect_path_normalization(self, jwt_verifier):
        """Test that redirect_path is normalized to start with /."""
        # Without leading slash
        proxy1 = OAuthProxy(
            upstream_authorization_endpoint="https://auth.com/authorize",
            upstream_token_endpoint="https://auth.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://server.com",
            redirect_path="auth/callback",
        )
        assert proxy1._redirect_path == "/auth/callback"

        # With leading slash
        proxy2 = OAuthProxy(
            upstream_authorization_endpoint="https://auth.com/authorize",
            upstream_token_endpoint="https://auth.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://server.com",
            redirect_path="/auth/callback",
        )
        assert proxy2._redirect_path == "/auth/callback"

    async def test_authorize_url_with_ampersand_separator(self, jwt_verifier):
        """Test that authorize builds URLs with & separator when upstream endpoint has existing query parameters."""
        # Test case: upstream endpoint with existing query parameters
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize?version=2.0",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
        )

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:54321/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="challenge",
            scopes=["read"],
        )

        # Should use "&" separator
        redirect_url = await proxy.authorize(client, params)
        parsed = urlparse(redirect_url)
        query_params = parse_qs(parsed.query)

        assert parsed.scheme == "https"
        assert parsed.netloc == "auth.example.com"
        assert parsed.path == "/authorize"
        # Params in the original url are kept
        assert "version" in query_params
        assert query_params["version"] == ["2.0"]
        # New params added correctly
        assert query_params["response_type"] == ["code"]

    def test_dcr_always_enabled(self, jwt_verifier):
        """Test that DCR is always enabled for OAuth Proxy."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.com/authorize",
            upstream_token_endpoint="https://auth.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://server.com",
        )

        assert proxy.client_registration_options is not None
        assert proxy.client_registration_options.enabled is True

    def test_revocation_enabled_with_endpoint(self, jwt_verifier):
        """Test that revocation is enabled when upstream endpoint is provided."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.com/authorize",
            upstream_token_endpoint="https://auth.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            upstream_revocation_endpoint="https://auth.com/revoke",
            token_verifier=jwt_verifier,
            base_url="https://server.com",
        )

        assert proxy.revocation_options is not None
        assert proxy.revocation_options.enabled is True
        assert proxy._upstream_revocation_endpoint == "https://auth.com/revoke"

    def test_revocation_disabled_without_endpoint(self, jwt_verifier):
        """Test that revocation is disabled when no upstream endpoint is provided."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.com/authorize",
            upstream_token_endpoint="https://auth.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://server.com",
        )

        assert proxy.revocation_options is None
        assert proxy._upstream_revocation_endpoint is None

    async def test_register_client(self, oauth_proxy):
        """Test client registration stores ProxyDCRClient without modifying original."""
        client_info = OAuthClientInformationFull(
            client_id="original-client-id",
            client_secret="original-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="client_secret_post",
        )

        await oauth_proxy.register_client(client_info)

        assert client_info.client_id == "original-client-id"
        assert client_info.client_secret == "original-secret"
        assert client_info.token_endpoint_auth_method == "client_secret_post"
        assert client_info.grant_types == ["authorization_code"]

        # Verify ProxyDCRClient was stored with original client credentials
        stored_client = oauth_proxy._clients.get("original-client-id")
        assert stored_client is not None
        assert stored_client.client_id == "original-client-id"
        assert stored_client.client_secret == "original-secret"
        assert stored_client.scope == "read write"

    async def test_register_client_empty_grant_types(self, oauth_proxy):
        """Test client registration with empty grant types."""
        client_info = OAuthClientInformationFull(
            client_id="original-client-id",
            client_secret="original-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            grant_types=[],  # Empty grant types list
        )

        await oauth_proxy.register_client(client_info)

        assert client_info.grant_types == []

        # Verify stored ProxyDCRClient has proper grant types
        stored_client = oauth_proxy._clients.get("original-client-id")
        assert stored_client is not None
        assert stored_client.grant_types == ["authorization_code", "refresh_token"]

    async def test_get_client_existing(self, oauth_proxy):
        """Test getting an existing registered client."""
        # Register a client first
        client_info = OAuthClientInformationFull(
            client_id="test-id",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await oauth_proxy.register_client(client_info)

        # Get the client
        retrieved = await oauth_proxy.get_client("test-id")
        assert retrieved is not None
        assert retrieved.client_id == "test-id"

    async def test_get_client_unregistered_returns_none(self, oauth_proxy):
        """Test that unregistered clients return None."""
        # Get a client that hasn't been registered
        client = await oauth_proxy.get_client("unknown-client-id")
        assert client is None

    async def test_authorize_creates_transaction(self, oauth_proxy):
        """Test that authorize creates a transaction and returns upstream URL."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:54321/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state-123",
            code_challenge="challenge-abc",
            scopes=["read", "write"],
        )

        # Call authorize
        redirect_url = await oauth_proxy.authorize(client, params)

        # Parse the redirect URL
        parsed = urlparse(redirect_url)
        query_params = parse_qs(parsed.query)

        # Verify it's redirecting to upstream
        assert parsed.scheme == "https"
        assert parsed.netloc == "github.com"
        assert parsed.path == "/login/oauth/authorize"

        # Verify query parameters
        assert query_params["response_type"] == ["code"]
        assert query_params["client_id"] == ["test-client-id"]
        assert query_params["redirect_uri"] == ["https://myserver.com/auth/callback"]
        assert "state" in query_params  # This should be the transaction ID
        assert query_params["scope"] == ["read write"]

        # Verify transaction was stored
        txn_id = query_params["state"][0]
        transaction = oauth_proxy._oauth_transactions.get(txn_id)
        assert transaction is not None
        assert transaction["client_id"] == "test-client"
        assert transaction["client_redirect_uri"] == "http://localhost:54321/callback"
        assert transaction["client_state"] == "client-state-123"
        assert transaction["code_challenge"] == "challenge-abc"
        assert transaction["code_challenge_method"] == "S256"
        assert transaction["scopes"] == ["read", "write"]

    async def test_authorize_without_scopes(self, oauth_proxy):
        """Test authorize without scopes uses required scopes from verifier."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:54321/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="challenge",
            scopes=[],  # Empty scopes to test fallback
        )

        redirect_url = await oauth_proxy.authorize(client, params)

        parsed = urlparse(redirect_url)
        query_params = parse_qs(parsed.query)

        # Should use required_scopes from token_verifier
        assert query_params["scope"] == ["read write"]

    async def test_authorize_no_scopes(self, jwt_verifier):
        """Test that proxy doesn't add scopes when none specified."""
        # Create proxy - using Google endpoints but proxy shouldn't special-case
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            upstream_token_endpoint="https://oauth2.googleapis.com/token",
            upstream_client_id="google-client",
            upstream_client_secret="google-secret",
            token_verifier=Mock(required_scopes=None),  # No required scopes
            base_url="https://myserver.com",
        )

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:54321/callback"),
            redirect_uri_provided_explicitly=True,
            state="state",
            code_challenge="challenge",
            scopes=[],  # Empty scopes
        )

        redirect_url = await proxy.authorize(client, params)

        parsed = urlparse(redirect_url)
        query_params = parse_qs(parsed.query)

        # Proxy should NOT add any scopes - providers handle their own defaults
        assert "scope" not in query_params

    async def test_client_scope_empty_when_no_required_scopes(self):
        """When required_scopes is None/empty, registered client scope should be empty string."""
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyUrl

        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=Mock(required_scopes=None),
            base_url="https://api.example.com",
        )

        # Register a client first
        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client_info)

        # Get the registered client
        registered_client = await proxy.get_client("test-client")
        assert registered_client is not None
        assert registered_client.scope == ""

    async def test_load_authorization_code_valid(self, oauth_proxy):
        """Test loading a valid authorization code."""
        # Store a client code
        code = "test-auth-code"
        oauth_proxy._client_codes[code] = {
            "client_id": "test-client-id",
            "redirect_uri": "http://localhost:54321/callback",
            "code_challenge": "challenge-123",
            "scopes": ["read", "write"],
            "expires_at": time.time() + 300,  # 5 minutes from now
            "idp_tokens": {"access_token": "token-123"},
        }

        client = OAuthClientInformationFull(
            client_id="test-client-id",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )

        # Load the code
        auth_code = await oauth_proxy.load_authorization_code(client, code)

        assert auth_code is not None
        assert auth_code.code == code
        assert auth_code.client_id == "test-client-id"
        assert str(auth_code.redirect_uri) == "http://localhost:54321/callback"
        assert auth_code.code_challenge == "challenge-123"
        assert auth_code.scopes == ["read", "write"]

    async def test_load_authorization_code_expired(self, oauth_proxy):
        """Test loading an expired authorization code returns None."""
        code = "expired-code"
        oauth_proxy._client_codes[code] = {
            "client_id": "test-client-id",
            "redirect_uri": "http://localhost:54321/callback",
            "expires_at": time.time() - 60,  # Expired 1 minute ago
        }

        client = OAuthClientInformationFull(
            client_id="test-client-id",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )

        auth_code = await oauth_proxy.load_authorization_code(client, code)
        assert auth_code is None
        # Code should be cleaned up
        assert code not in oauth_proxy._client_codes

    async def test_load_authorization_code_wrong_client(self, oauth_proxy):
        """Test loading authorization code with wrong client ID returns None."""
        code = "test-code"
        oauth_proxy._client_codes[code] = {
            "client_id": "correct-client-id",
            "redirect_uri": "http://localhost:54321/callback",
            "expires_at": time.time() + 300,
        }

        wrong_client = OAuthClientInformationFull(
            client_id="wrong-client-id",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )

        auth_code = await oauth_proxy.load_authorization_code(wrong_client, code)
        assert auth_code is None

    async def test_load_access_token_delegates_to_verifier(
        self, oauth_proxy, jwt_verifier
    ):
        """Test that load_access_token delegates to the token verifier."""
        token = "test-access-token"
        expected_result = AccessToken(
            token=token,
            client_id="test-client",
            scopes=["read"],
            expires_at=int(time.time() + 3600),
        )

        # Mock the async method properly
        async def mock_verify(token):
            return expected_result

        jwt_verifier.verify_token = mock_verify

        result = await oauth_proxy.load_access_token(token)

        assert result == expected_result
        # Can't assert on the mock function call in this case

    def test_get_routes_includes_callback(self, oauth_proxy):
        """Test that get_routes includes the OAuth callback route."""
        routes = oauth_proxy.get_routes()

        # Find the callback route
        callback_routes = [
            r for r in routes if hasattr(r, "path") and r.path == "/auth/callback"
        ]

        assert len(callback_routes) == 1
        callback_route = callback_routes[0]
        assert "GET" in callback_route.methods
        assert callback_route.endpoint == oauth_proxy._handle_idp_callback

    def test_get_routes_preserves_standard_routes(self, oauth_proxy):
        """Test that get_routes preserves standard OAuth routes."""
        routes = oauth_proxy.get_routes()

        # Should have standard OAuth routes
        paths = [r.path for r in routes if hasattr(r, "path")]

        # Standard OAuth endpoints should be present
        assert "/authorize" in paths
        assert "/token" in paths
        assert "/.well-known/oauth-authorization-server" in paths

        # Plus our custom callback
        assert "/auth/callback" in paths

    async def test_revoke_token_access_token(self, oauth_proxy):
        """Test revoking an access token cleans up local storage."""
        # Store tokens
        access_token = "access-123"
        refresh_token = "refresh-456"

        oauth_proxy._access_tokens[access_token] = AccessToken(
            token=access_token,
            client_id="client",
            scopes=[],
            expires_at=int(time.time() + 3600),
        )
        oauth_proxy._refresh_tokens[refresh_token] = Mock(token=refresh_token)
        oauth_proxy._access_to_refresh[access_token] = refresh_token
        oauth_proxy._refresh_to_access[refresh_token] = access_token

        # Revoke access token
        await oauth_proxy.revoke_token(oauth_proxy._access_tokens[access_token])

        # Verify cleanup
        assert access_token not in oauth_proxy._access_tokens
        assert refresh_token not in oauth_proxy._refresh_tokens
        assert access_token not in oauth_proxy._access_to_refresh
        assert refresh_token not in oauth_proxy._refresh_to_access

    async def test_exchange_authorization_code_stores_tokens(self, oauth_proxy):
        """Test that exchange_authorization_code stores tokens locally."""
        from mcp.server.auth.provider import AuthorizationCode

        # Set up client code with IdP tokens
        code = "client-code-123"
        idp_tokens = {
            "access_token": "idp-access-token",
            "refresh_token": "idp-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        oauth_proxy._client_codes[code] = {
            "client_id": "test-client",
            "redirect_uri": "http://localhost:54321/callback",
            "scopes": ["read", "write"],
            "idp_tokens": idp_tokens,
            "expires_at": time.time() + 300,
        }

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )

        auth_code = AuthorizationCode(
            code=code,
            client_id="test-client",
            redirect_uri=AnyUrl("http://localhost:54321/callback"),
            redirect_uri_provided_explicitly=True,
            scopes=["read", "write"],
            expires_at=time.time() + 300,
            code_challenge="test-challenge",
        )

        # Exchange the code
        result = await oauth_proxy.exchange_authorization_code(client, auth_code)

        # Verify result
        assert result.access_token == "idp-access-token"
        assert result.refresh_token == "idp-refresh-token"
        assert result.expires_in == 3600

        # Verify tokens were stored locally
        assert "idp-access-token" in oauth_proxy._access_tokens
        assert "idp-refresh-token" in oauth_proxy._refresh_tokens
        assert oauth_proxy._access_to_refresh["idp-access-token"] == "idp-refresh-token"
        assert oauth_proxy._refresh_to_access["idp-refresh-token"] == "idp-access-token"

        # Verify code was cleaned up
        assert code not in oauth_proxy._client_codes


class TestOAuthProxyPKCE:
    """Test suite for OAuth Proxy PKCE forwarding functionality."""

    @pytest.fixture
    def jwt_verifier(self):
        """Create a mock JWT verifier for testing."""
        verifier = Mock()
        verifier.required_scopes = ["read", "write"]
        return verifier

    @pytest.fixture
    def oauth_proxy_with_pkce(self, jwt_verifier):
        """Create an OAuthProxy instance with PKCE forwarding enabled."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            forward_pkce=True,  # Enable PKCE forwarding
        )

    @pytest.fixture
    def oauth_proxy_without_pkce(self, jwt_verifier):
        """Create an OAuthProxy instance with PKCE forwarding disabled."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            forward_pkce=False,  # Disable PKCE forwarding
        )

    async def test_pkce_forwarding_enabled(self, oauth_proxy_with_pkce):
        """Test that proxy generates and forwards its own PKCE when client uses PKCE."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge_value",
            code_challenge_method="S256",
            scopes=["read"],
        )

        redirect_url = await oauth_proxy_with_pkce.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Verify proxy forwards its own PKCE challenge
        assert "code_challenge" in query_params
        assert query_params["code_challenge"][0] != "client_challenge_value"
        assert query_params["code_challenge_method"] == ["S256"]

        # Verify transaction stores both client and proxy PKCE
        txn_id = query_params["state"][0]
        transaction = oauth_proxy_with_pkce._oauth_transactions[txn_id]
        assert transaction["code_challenge"] == "client_challenge_value"  # Client's
        assert "proxy_code_verifier" in transaction  # Proxy's verifier stored

    async def test_pkce_forwarding_disabled(self, oauth_proxy_without_pkce):
        """Test that PKCE is not forwarded when forward_pkce=False."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge_value",
            scopes=["read"],
        )

        redirect_url = await oauth_proxy_without_pkce.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Verify NO PKCE parameters forwarded to upstream
        assert "code_challenge" not in query_params
        assert "code_challenge_method" not in query_params

        # But client's PKCE is still stored for validation
        txn_id = query_params["state"][0]
        transaction = oauth_proxy_without_pkce._oauth_transactions[txn_id]
        assert transaction["code_challenge"] == "client_challenge_value"
        assert "proxy_code_verifier" not in transaction

    async def test_no_pkce_when_client_has_none(self, oauth_proxy_with_pkce):
        """Test that proxy doesn't generate PKCE if client doesn't use it."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="",  # Empty string means no PKCE
            scopes=["read"],
        )

        redirect_url = await oauth_proxy_with_pkce.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Verify NO PKCE forwarded
        assert "code_challenge" not in query_params
        assert "code_challenge_method" not in query_params

        # No proxy verifier stored
        txn_id = query_params["state"][0]
        transaction = oauth_proxy_with_pkce._oauth_transactions[txn_id]
        assert "proxy_code_verifier" not in transaction


class TestOAuthProxyTokenAuthMethod:
    """Test suite for OAuth Proxy token_endpoint_auth_method parameter."""

    @pytest.fixture
    def jwt_verifier(self):
        """Create a mock JWT verifier for testing."""
        verifier = Mock()
        verifier.required_scopes = ["read", "write"]
        return verifier

    def test_initialization_with_token_auth_method(self, jwt_verifier):
        """Test that token_endpoint_auth_method is stored correctly when provided."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client-id",
            upstream_client_secret="client-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            token_endpoint_auth_method="client_secret_post",
        )

        assert proxy._token_endpoint_auth_method == "client_secret_post"

    def test_initialization_without_token_auth_method(self, jwt_verifier):
        """Test that token_endpoint_auth_method defaults to None when not provided."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client-id",
            upstream_client_secret="client-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
        )

        assert proxy._token_endpoint_auth_method is None

    def test_different_auth_methods(self, jwt_verifier):
        """Test initialization with different authentication methods."""
        # Test client_secret_basic
        proxy_basic = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client-id",
            upstream_client_secret="client-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            token_endpoint_auth_method="client_secret_basic",
        )
        assert proxy_basic._token_endpoint_auth_method == "client_secret_basic"

        # Test none (for public clients)
        proxy_none = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client-id",
            upstream_client_secret="client-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            token_endpoint_auth_method="none",
        )
        assert proxy_none._token_endpoint_auth_method == "none"

    @pytest.mark.asyncio
    async def test_token_auth_method_passed_to_oauth_client(self, jwt_verifier):
        """Test that token_endpoint_auth_method is passed to AsyncOAuth2Client during token exchange."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client-id",
            upstream_client_secret="client-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            token_endpoint_auth_method="client_secret_post",
        )

        # Mock AsyncOAuth2Client
        with patch(
            "fastmcp.server.auth.oauth_proxy.AsyncOAuth2Client"
        ) as MockOAuth2Client:
            mock_client = AsyncMock()
            mock_client.refresh_token = AsyncMock(
                return_value={
                    "access_token": "new-access-token",
                    "refresh_token": "new-refresh-token",
                    "expires_in": 3600,
                }
            )
            MockOAuth2Client.return_value = mock_client

            # Test exchange_refresh_token
            client = OAuthClientInformationFull(
                client_id="test-client",
                client_secret="test-secret",
                redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            )

            refresh_token = RefreshToken(
                token="old-refresh-token",
                client_id="test-client",
                scopes=["read"],
                expires_at=None,
            )

            await proxy.exchange_refresh_token(client, refresh_token, ["read"])

            # Verify AsyncOAuth2Client was called with token_endpoint_auth_method
            MockOAuth2Client.assert_called_with(
                client_id="client-id",
                client_secret="client-secret",
                token_endpoint_auth_method="client_secret_post",
                timeout=30.0,
            )

    @pytest.mark.asyncio
    async def test_token_auth_method_in_idp_callback(self, jwt_verifier):
        """Test that token_endpoint_auth_method is passed during IdP callback handling."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="client-id",
            upstream_client_secret="client-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            token_endpoint_auth_method="client_secret_basic",
        )

        # Set up OAuth transaction
        txn_id = "test-transaction"
        proxy._oauth_transactions[txn_id] = {
            "client_id": "test-client",
            "redirect_uri": "http://localhost:12345/callback",
            "state": "client-state",
            "scopes": ["read"],
            "proxy_code_verifier": "verifier123",
        }

        # Register client
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client)

        # Mock AsyncOAuth2Client and Request
        with patch(
            "fastmcp.server.auth.oauth_proxy.AsyncOAuth2Client"
        ) as MockOAuth2Client:
            mock_client = AsyncMock()
            mock_client.fetch_token = AsyncMock(
                return_value={
                    "access_token": "idp-access-token",
                    "refresh_token": "idp-refresh-token",
                    "expires_in": 3600,
                }
            )
            MockOAuth2Client.return_value = mock_client

            # Create mock request with query params
            mock_request = Mock()
            mock_request.query_params = {"code": "idp-auth-code", "state": txn_id}

            # Simulate IdP callback
            await proxy._handle_idp_callback(mock_request)

            # Verify AsyncOAuth2Client was called with token_endpoint_auth_method
            MockOAuth2Client.assert_called_with(
                client_id="client-id",
                client_secret="client-secret",
                token_endpoint_auth_method="client_secret_basic",
                timeout=30.0,
            )
