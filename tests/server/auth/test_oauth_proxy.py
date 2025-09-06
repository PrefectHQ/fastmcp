"""Comprehensive tests for OAuth Proxy Provider functionality.

This test suite covers:
1. Initialization and configuration
2. Client registration (DCR)
3. Authorization flow
4. Token management
5. PKCE forwarding
6. Token endpoint authentication methods
7. E2E testing with mock OAuth provider
"""

import asyncio
import secrets
import time
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken, RefreshToken, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier

# =============================================================================
# Mock OAuth Provider for E2E Testing
# =============================================================================


class MockOAuthProvider:
    """Mock OAuth provider for testing OAuth proxy E2E flows.

    This provider simulates a complete OAuth server without requiring:
    - Real authentication credentials
    - Browser automation
    - Network calls to external services
    """

    def __init__(self, port: int = 9999):
        self.port = port
        self.base_url = f"http://localhost:{port}"
        self.app = None
        self.server = None

        # Storage for OAuth state
        self.authorization_codes = {}
        self.access_tokens = {}
        self.refresh_tokens = {}
        self.revoked_tokens = set()

        # Tracking for assertions
        self.authorize_called = False
        self.token_called = False
        self.refresh_called = False
        self.revoke_called = False

        # Configuration
        self.require_pkce = False
        self.token_endpoint_auth_method = "client_secret_basic"

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.base_url}/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.base_url}/token"

    @property
    def revocation_endpoint(self) -> str:
        return f"{self.base_url}/revoke"

    def create_app(self) -> Starlette:
        """Create the mock OAuth server application."""
        return Starlette(
            routes=[
                Route("/authorize", self.handle_authorize),
                Route("/token", self.handle_token, methods=["POST"]),
                Route("/revoke", self.handle_revoke, methods=["POST"]),
            ]
        )

    async def handle_authorize(self, request):
        """Handle authorization requests."""
        self.authorize_called = True
        query = dict(request.query_params)

        # Validate PKCE if required
        if self.require_pkce and "code_challenge" not in query:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "PKCE required"},
                status_code=400,
            )

        # Generate authorization code
        code = secrets.token_urlsafe(32)
        self.authorization_codes[code] = {
            "client_id": query.get("client_id"),
            "redirect_uri": query.get("redirect_uri"),
            "state": query.get("state"),
            "code_challenge": query.get("code_challenge"),
            "code_challenge_method": query.get("code_challenge_method", "S256"),
            "scope": query.get("scope"),
            "created_at": time.time(),
        }

        # Redirect back to callback
        redirect_uri = query["redirect_uri"]
        params = {"code": code}
        if query.get("state"):
            params["state"] = query["state"]

        redirect_url = f"{redirect_uri}?{urlencode(params)}"
        return JSONResponse(
            content={}, status_code=302, headers={"Location": redirect_url}
        )

    async def handle_token(self, request):
        """Handle token requests."""
        self.token_called = True
        form = await request.form()
        grant_type = form.get("grant_type")

        if grant_type == "authorization_code":
            code = form.get("code")
            if code not in self.authorization_codes:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid code"},
                    status_code=400,
                )

            # Validate PKCE if it was used
            auth_data = self.authorization_codes[code]
            if auth_data.get("code_challenge"):
                verifier = form.get("code_verifier")
                if not verifier:
                    return JSONResponse(
                        {
                            "error": "invalid_request",
                            "error_description": "Missing code_verifier",
                        },
                        status_code=400,
                    )
                # In a real implementation, we'd validate the verifier

            # Generate tokens
            access_token = f"mock_access_{secrets.token_hex(16)}"
            refresh_token = f"mock_refresh_{secrets.token_hex(16)}"

            self.access_tokens[access_token] = {
                "client_id": auth_data["client_id"],
                "scope": auth_data.get("scope"),
                "expires_at": time.time() + 3600,
            }
            self.refresh_tokens[refresh_token] = {
                "client_id": auth_data["client_id"],
                "scope": auth_data.get("scope"),
            }

            # Clean up used code
            del self.authorization_codes[code]

            return JSONResponse(
                {
                    "access_token": access_token,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": refresh_token,
                    "scope": auth_data.get("scope"),
                }
            )

        elif grant_type == "refresh_token":
            self.refresh_called = True
            refresh_token = form.get("refresh_token")

            if refresh_token not in self.refresh_tokens:
                return JSONResponse(
                    {
                        "error": "invalid_grant",
                        "error_description": "Invalid refresh token",
                    },
                    status_code=400,
                )

            # Generate new access token
            new_access = f"mock_access_{secrets.token_hex(16)}"
            token_data = self.refresh_tokens[refresh_token]

            self.access_tokens[new_access] = {
                "client_id": token_data["client_id"],
                "scope": token_data.get("scope"),
                "expires_at": time.time() + 3600,
            }

            return JSONResponse(
                {
                    "access_token": new_access,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": refresh_token,  # Same refresh token
                    "scope": token_data.get("scope"),
                }
            )

        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    async def handle_revoke(self, request):
        """Handle token revocation."""
        self.revoke_called = True
        form = await request.form()
        token = form.get("token")

        if token:
            self.revoked_tokens.add(token)
            # Remove from active tokens
            self.access_tokens.pop(token, None)
            self.refresh_tokens.pop(token, None)

        return JSONResponse({})

    async def start(self):
        """Start the mock OAuth server."""
        from uvicorn import Config, Server

        self.app = self.create_app()
        config = Config(self.app, host="localhost", port=self.port, log_level="error")
        self.server = Server(config)

        # Start server in background
        asyncio.create_task(self.server.serve())

        # Wait for server to be ready
        await asyncio.sleep(0.5)

    async def stop(self):
        """Stop the mock OAuth server."""
        if self.server:
            self.server.should_exit = True
            await asyncio.sleep(0.1)

    def reset(self):
        """Reset all state for next test."""
        self.authorization_codes.clear()
        self.access_tokens.clear()
        self.refresh_tokens.clear()
        self.revoked_tokens.clear()
        self.authorize_called = False
        self.token_called = False
        self.refresh_called = False
        self.revoke_called = False


class MockTokenVerifier(TokenVerifier):
    """Mock token verifier for testing."""

    def __init__(self, required_scopes=None):
        self.required_scopes = required_scopes or ["read", "write"]
        self.verify_called = False

    async def verify_token(self, token: str) -> AccessToken:
        """Mock token verification."""
        self.verify_called = True
        return AccessToken(
            token=token,
            client_id="mock-client",
            scopes=self.required_scopes,
            expires_at=int(time.time() + 3600),
        )


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def jwt_verifier():
    """Create a mock JWT verifier for testing."""
    verifier = Mock(spec=JWTVerifier)
    verifier.required_scopes = ["read", "write"]
    verifier.verify_token = Mock(return_value=None)
    return verifier


@pytest.fixture
def oauth_proxy(jwt_verifier):
    """Create a standard OAuthProxy instance for testing."""
    return OAuthProxy(
        upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
        upstream_token_endpoint="https://github.com/login/oauth/access_token",
        upstream_client_id="test-client-id",
        upstream_client_secret="test-client-secret",
        token_verifier=jwt_verifier,
        base_url="https://myserver.com",
        redirect_path="/auth/callback",
    )


@pytest.fixture
async def mock_oauth_provider():
    """Create and start a mock OAuth provider."""
    provider = MockOAuthProvider(port=9999)
    await provider.start()
    yield provider
    await provider.stop()


# =============================================================================
# Test Classes
# =============================================================================


class TestOAuthProxyInitialization:
    """Tests for OAuth proxy initialization and configuration."""

    def test_basic_initialization(self, jwt_verifier):
        """Test basic proxy initialization with required parameters."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
        )

        assert (
            proxy._upstream_authorization_endpoint
            == "https://auth.example.com/authorize"
        )
        assert proxy._upstream_token_endpoint == "https://auth.example.com/token"
        assert proxy._upstream_client_id == "client-123"
        assert proxy._upstream_client_secret.get_secret_value() == "secret-456"
        assert str(proxy.base_url) == "https://api.example.com/"

    def test_all_optional_parameters(self, jwt_verifier):
        """Test initialization with all optional parameters."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            upstream_revocation_endpoint="https://auth.example.com/revoke",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            redirect_path="/custom/callback",
            issuer_url="https://issuer.example.com",
            service_documentation_url="https://docs.example.com",
            allowed_client_redirect_uris=["http://localhost:*"],
            valid_scopes=["custom", "scopes"],
            forward_pkce=False,
            token_endpoint_auth_method="client_secret_post",
        )

        assert proxy._upstream_revocation_endpoint == "https://auth.example.com/revoke"
        assert proxy._redirect_path == "/custom/callback"
        assert proxy._forward_pkce is False
        assert proxy._token_endpoint_auth_method == "client_secret_post"
        assert proxy.client_registration_options.valid_scopes == ["custom", "scopes"]

    def test_redirect_path_normalization(self, jwt_verifier):
        """Test that redirect_path is normalized with leading slash."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.com/authorize",
            upstream_token_endpoint="https://auth.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://api.com",
            redirect_path="auth/callback",  # No leading slash
        )
        assert proxy._redirect_path == "/auth/callback"


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

        # Client should be stored with original credentials
        stored = oauth_proxy._clients.get("original-client")
        assert stored is not None
        assert stored.client_id == "original-client"
        assert stored.client_secret == "original-secret"

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


class TestOAuthProxyAuthorization:
    """Tests for OAuth proxy authorization flow."""

    async def test_authorize_creates_transaction(self, oauth_proxy):
        """Test that authorize creates transaction and returns upstream URL."""
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
            code_challenge_method="S256",
            scopes=["read", "write"],
        )

        redirect_url = await oauth_proxy.authorize(client, params)

        # Parse the redirect URL
        parsed = urlparse(redirect_url)
        query_params = parse_qs(parsed.query)

        # Verify upstream URL structure
        assert "github.com/login/oauth/authorize" in redirect_url
        assert query_params["client_id"][0] == "test-client-id"
        assert query_params["response_type"][0] == "code"
        assert "state" in query_params  # Transaction ID

        # Verify transaction was stored
        txn_id = query_params["state"][0]
        assert txn_id in oauth_proxy._oauth_transactions
        transaction = oauth_proxy._oauth_transactions[txn_id]
        assert transaction["client_id"] == "test-client"
        assert transaction["code_challenge"] == "challenge-abc"


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
        )

    @pytest.fixture
    def proxy_without_pkce(self, jwt_verifier):
        return OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            forward_pkce=False,
        )

    async def test_pkce_forwarding_enabled(self, proxy_with_pkce):
        """Test that proxy generates and forwards its own PKCE."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
        )

        redirect_url = await proxy_with_pkce.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Proxy should forward its own PKCE
        assert "code_challenge" in query_params
        assert query_params["code_challenge"][0] != "client_challenge"
        assert query_params["code_challenge_method"] == ["S256"]

        # Transaction should store both challenges
        txn_id = query_params["state"][0]
        transaction = proxy_with_pkce._oauth_transactions[txn_id]
        assert transaction["code_challenge"] == "client_challenge"  # Client's
        assert "proxy_code_verifier" in transaction  # Proxy's verifier

    async def test_pkce_forwarding_disabled(self, proxy_without_pkce):
        """Test that PKCE is not forwarded when disabled."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
        )

        redirect_url = await proxy_without_pkce.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # No PKCE forwarded to upstream
        assert "code_challenge" not in query_params
        assert "code_challenge_method" not in query_params

        # Client's challenge still stored
        txn_id = query_params["state"][0]
        transaction = proxy_without_pkce._oauth_transactions[txn_id]
        assert transaction["code_challenge"] == "client_challenge"
        assert "proxy_code_verifier" not in transaction


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
        )
        assert proxy_default._token_endpoint_auth_method is None

    @pytest.mark.asyncio
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
        )

        with patch("fastmcp.server.auth.oauth_proxy.AsyncOAuth2Client") as MockClient:
            mock_client = AsyncMock()
            mock_client.refresh_token = AsyncMock(
                return_value={
                    "access_token": "new-token",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                }
            )
            MockClient.return_value = mock_client

            client = OAuthClientInformationFull(
                client_id="test-client",
                client_secret="test-secret",
                redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            )

            refresh_token = RefreshToken(
                token="old-refresh",
                client_id="test-client",
                scopes=["read"],
                expires_at=None,
            )

            await proxy.exchange_refresh_token(client, refresh_token, ["read"])

            # Verify auth method was passed
            MockClient.assert_called_with(
                client_id="client-id",
                client_secret="client-secret",
                token_endpoint_auth_method="client_secret_post",
                timeout=30.0,
            )


class TestOAuthProxyE2E:
    """End-to-end tests using mock OAuth provider."""

    @pytest.mark.asyncio
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

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="",  # Empty string for no PKCE
            scopes=["read"],
        )

        # Get authorization URL
        auth_url = await proxy.authorize(client_info, params)

        # Verify mock provider was called
        assert mock_oauth_provider.authorize_endpoint in auth_url

        # Verify state is present (transaction ID)
        query_params = parse_qs(urlparse(auth_url).query)
        assert "state" in query_params

        # Simulate authorization callback
        async with httpx.AsyncClient() as http_client:
            # This would normally redirect, but our mock returns the code
            response = await http_client.get(auth_url, follow_redirects=False)

            # Extract code from redirect location
            location = response.headers.get("location", "")
            callback_params = parse_qs(urlparse(location).query)
            auth_code = callback_params.get("code", [None])[0]

            assert auth_code is not None
            assert mock_oauth_provider.authorize_called

    @pytest.mark.asyncio
    async def test_token_refresh_with_mock_provider(self, mock_oauth_provider):
        """Test token refresh flow with mock provider."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint=mock_oauth_provider.authorize_endpoint,
            upstream_token_endpoint=mock_oauth_provider.token_endpoint,
            upstream_client_id="mock-client",
            upstream_client_secret="mock-secret",
            token_verifier=MockTokenVerifier(),
            base_url="http://localhost:8000",
        )

        # Mock initial tokens in provider
        refresh_token = "mock_refresh_initial"
        mock_oauth_provider.refresh_tokens[refresh_token] = {
            "client_id": "mock-client",
            "scope": "read write",
        }

        with patch("fastmcp.server.auth.oauth_proxy.AsyncOAuth2Client") as MockClient:
            mock_client = AsyncMock()

            # Configure mock to call real provider
            async def mock_refresh(*args, **kwargs):
                async with httpx.AsyncClient() as http:
                    response = await http.post(
                        mock_oauth_provider.token_endpoint,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": refresh_token,
                        },
                    )
                    return response.json()

            mock_client.refresh_token = mock_refresh
            MockClient.return_value = mock_client

            # Test refresh
            client = OAuthClientInformationFull(
                client_id="test-client",
                client_secret="test-secret",
                redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            )

            refresh = RefreshToken(
                token=refresh_token,
                client_id="test-client",
                scopes=["read"],
                expires_at=None,
            )

            result = await proxy.exchange_refresh_token(client, refresh, ["read"])

            assert result.access_token.startswith("mock_access_")
            assert mock_oauth_provider.refresh_called

    @pytest.mark.asyncio
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
        )

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

        # Start authorization with PKCE
        auth_url = await proxy.authorize(client, params)
        query_params = parse_qs(urlparse(auth_url).query)

        # Verify PKCE was forwarded (proxy's challenge, not client's)
        assert "code_challenge" in query_params
        assert query_params["code_challenge"][0] != "client_challenge_value"

        # Transaction should have proxy's verifier
        txn_id = query_params["state"][0]
        transaction = proxy._oauth_transactions[txn_id]
        assert "proxy_code_verifier" in transaction


class TestParameterForwarding:
    """Tests for forwarding custom parameters to upstream OAuth provider."""

    @pytest.fixture
    def proxy_with_extra_params(self, jwt_verifier):
        """Create OAuthProxy with extra parameters configured."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            extra_authorize_params={"audience": "https://api.example.com"},
            extra_token_params={"audience": "https://api.example.com"},
        )

    @pytest.fixture
    def proxy_without_extra_params(self, jwt_verifier):
        """Create OAuthProxy without extra parameters."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
        )

    async def test_resource_parameter_forwarding(self, proxy_without_extra_params):
        """Test that RFC 8707 resource parameter is forwarded from client request."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
            resource="https://api.example.com/v1",  # RFC 8707 resource indicator
        )

        redirect_url = await proxy_without_extra_params.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Resource parameter should be forwarded to upstream
        assert "resource" in query_params
        assert query_params["resource"][0] == "https://api.example.com/v1"

    async def test_extra_authorize_params(self, proxy_with_extra_params):
        """Test that extra authorization parameters are included."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
        )

        redirect_url = await proxy_with_extra_params.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Extra audience parameter should be included
        assert "audience" in query_params
        assert query_params["audience"][0] == "https://api.example.com"

    async def test_resource_and_extra_params_together(self, proxy_with_extra_params):
        """Test that both resource and extra params can be used together."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
            resource="https://resource.example.com",  # Client-specified resource
        )

        redirect_url = await proxy_with_extra_params.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Both resource and audience should be present
        assert "resource" in query_params
        assert query_params["resource"][0] == "https://resource.example.com"
        assert "audience" in query_params
        assert query_params["audience"][0] == "https://api.example.com"

    async def test_no_extra_params_when_not_configured(
        self, proxy_without_extra_params
    ):
        """Test that no extra params are added when not configured."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
            # No resource parameter
        )

        redirect_url = await proxy_without_extra_params.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # No audience parameter should be present (not configured)
        assert "audience" not in query_params
        # No resource parameter should be present (not provided by client)
        assert "resource" not in query_params

    async def test_multiple_extra_params(self, jwt_verifier):
        """Test multiple extra parameters can be configured and forwarded."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
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

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
        )

        redirect_url = await proxy.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # All extra parameters should be included
        assert query_params["audience"][0] == "https://api.example.com"
        assert query_params["prompt"][0] == "consent"
        assert query_params["max_age"][0] == "3600"
