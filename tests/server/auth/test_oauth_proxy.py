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

    def __init__(self, port: int = 0):
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
        import socket

        from uvicorn import Config, Server

        self.app = self.create_app()

        # If port is 0, find an available port
        if self.port == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                s.listen(1)
                self.port = s.getsockname()[1]

        self.base_url = f"http://localhost:{self.port}"
        config = Config(
            self.app,
            host="localhost",
            port=self.port,
            log_level="error",
            ws="websockets-sansio",
        )
        self.server = Server(config)

        # Start server in background
        asyncio.create_task(self.server.serve())

        # Wait for server to be ready
        await asyncio.sleep(0.05)

    async def stop(self):
        """Stop the mock OAuth server."""
        if self.server:
            self.server.should_exit = True
            await asyncio.sleep(0.01)

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
        jwt_signing_key="test-secret",
    )


@pytest.fixture
async def mock_oauth_provider():
    """Create and start a mock OAuth provider."""
    provider = MockOAuthProvider()
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
            jwt_signing_key="test-secret",
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
            jwt_signing_key="test-secret",
        )

        assert proxy._upstream_revocation_endpoint == "https://auth.example.com/revoke"
        assert proxy._redirect_path == "/custom/callback"
        assert proxy._forward_pkce is False
        assert proxy._token_endpoint_auth_method == "client_secret_post"
        assert proxy.client_registration_options is not None
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
            jwt_signing_key="test-secret",
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


class TestOAuthProxyAuthorization:
    """Tests for OAuth proxy authorization flow."""

    async def test_authorize_creates_transaction(self, oauth_proxy):
        """Test that authorize creates transaction and redirects to consent."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
            jwt_signing_key="test-secret",
        )

        # Register client first (required for consent flow)
        await oauth_proxy.register_client(client)

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

        # First, create a valid FastMCP token via full OAuth flow
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Mock the upstream OAuth provider response
        with patch("fastmcp.server.auth.oauth_proxy.AsyncOAuth2Client") as MockClient:
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
            from fastmcp.server.auth.oauth_proxy import ClientCode

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
            from mcp.server.auth.provider import AuthorizationCode

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

        with patch("fastmcp.server.auth.oauth_proxy.AsyncOAuth2Client") as MockClient:
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
            from fastmcp.server.auth.oauth_proxy import ClientCode

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
            from mcp.server.auth.provider import AuthorizationCode

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
            code_challenge_method="S256",
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
            jwt_signing_key="test-secret",
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
            jwt_signing_key="test-secret",
        )

    async def test_resource_parameter_forwarding(self, proxy_without_extra_params):
        """Test that RFC 8707 resource parameter is forwarded from client request."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Register client first
        await proxy_without_extra_params.register_client(client)

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

        # Should redirect to consent page
        assert "/consent" in redirect_url
        assert "txn_id" in query_params

        # Resource parameter should be stored in transaction for upstream forwarding
        txn_id = query_params["txn_id"][0]
        transaction = await proxy_without_extra_params._transaction_store.get(
            key=txn_id
        )
        assert transaction is not None
        assert transaction.resource == "https://api.example.com/v1"

    async def test_extra_authorize_params(self, proxy_with_extra_params):
        """Test that extra authorization parameters are included."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Register client first
        await proxy_with_extra_params.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="client_challenge",
            scopes=["read"],
        )

        redirect_url = await proxy_with_extra_params.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Should redirect to consent page
        assert "/consent" in redirect_url
        assert "txn_id" in query_params

        # Extra audience parameter is configured at proxy level (not per-transaction)
        txn_id = query_params["txn_id"][0]
        transaction = await proxy_with_extra_params._transaction_store.get(key=txn_id)
        assert transaction is not None
        # Verify proxy has extra params configured
        assert (
            proxy_with_extra_params._extra_authorize_params.get("audience")
            == "https://api.example.com"
        )

    async def test_resource_and_extra_params_together(self, proxy_with_extra_params):
        """Test that both resource and extra params can be used together."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Register client first
        await proxy_with_extra_params.register_client(client)

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

        # Should redirect to consent page
        assert "/consent" in redirect_url
        assert "txn_id" in query_params

        # Resource stored in transaction, extra params configured at proxy level
        txn_id = query_params["txn_id"][0]
        transaction = await proxy_with_extra_params._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert transaction.resource == "https://resource.example.com"
        assert (
            proxy_with_extra_params._extra_authorize_params.get("audience")
            == "https://api.example.com"
        )

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
            code_challenge="client_challenge",
            scopes=["read"],
        )

        redirect_url = await proxy.authorize(client, params)
        query_params = parse_qs(urlparse(redirect_url).query)

        # Should redirect to consent page
        assert "/consent" in redirect_url
        assert "txn_id" in query_params

        # All extra parameters configured at proxy level
        txn_id = query_params["txn_id"][0]
        transaction = await proxy._transaction_store.get(key=txn_id)
        assert transaction is not None
        # Verify proxy has all extra params configured
        assert (
            proxy._extra_authorize_params.get("audience") == "https://api.example.com"
        )
        assert proxy._extra_authorize_params.get("prompt") == "consent"
        assert proxy._extra_authorize_params.get("max_age") == "3600"

    async def test_token_endpoint_invalid_client_error(self, jwt_verifier):
        """Test that invalid client_id returns OAuth 2.1 compliant error response.

        When a client ID is not found during token exchange, the proxy should:
        1. Return HTTP 401 status code
        2. Use 'invalid_client' error code instead of 'unauthorized_client'

        This aligns with OAuth 2.1 spec and enables Claude's automatic client re-registration.
        """
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret",
        )

        # Create a test app with OAuth routes
        app = Starlette(routes=proxy.get_routes())

        # Test the token endpoint with an invalid (non-existent) client_id
        with TestClient(app) as client:
            response = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": "test-auth-code",
                    "client_id": "non-existent-client-id",
                    "code_verifier": "test-code-verifier",
                    "redirect_uri": "http://localhost:12345/callback",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

            # Verify OAuth 2.1 compliant error response
            assert response.status_code == 401, (
                f"Expected 401 but got {response.status_code}"
            )

            error_data = response.json()
            assert error_data["error"] == "invalid_client", (
                f"Expected 'invalid_client' but got '{error_data.get('error')}'"
            )
            assert "Invalid client_id" in error_data["error_description"]

            # Verify proper cache headers are set
            assert response.headers.get("Cache-Control") == "no-store"
            assert response.headers.get("Pragma") == "no-cache"


class TestTokenHandlerErrorTransformation:
    """Tests for TokenHandler's OAuth 2.1 compliant error transformation."""

    async def test_transforms_client_auth_failure_to_invalid_client_401(self):
        """Test that client authentication failures return invalid_client with 401."""
        from unittest.mock import AsyncMock, patch

        from mcp.server.auth.handlers.token import TokenHandler as SDKTokenHandler

        from fastmcp.server.auth.auth import TokenHandler

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
        from mcp.server.auth.handlers.token import TokenErrorResponse

        from fastmcp.server.auth.auth import TokenHandler

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

    def test_does_not_transform_other_errors(self):
        """Test that other error types pass through unchanged."""
        from mcp.server.auth.handlers.token import TokenErrorResponse

        from fastmcp.server.auth.auth import TokenHandler

        handler = TokenHandler(provider=Mock(), client_authenticator=Mock())

        error_response = TokenErrorResponse(
            error="invalid_grant",
            error_description="Authorization code has expired",
        )

        response = handler.response(error_response)

        # Should pass through unchanged
        assert response.status_code == 400
        assert b'"error":"invalid_grant"' in response.body


class TestErrorPageRendering:
    """Test error page rendering for OAuth callback errors."""

    def test_create_error_html_basic(self):
        """Test basic error page generation."""
        from fastmcp.server.auth.oauth_proxy import create_error_html

        html = create_error_html(
            error_title="Test Error",
            error_message="This is a test error message",
        )

        # Verify it's valid HTML
        assert "<!DOCTYPE html>" in html
        assert "<title>Test Error</title>" in html
        assert "This is a test error message" in html
        assert 'class="info-box error"' in html

    def test_create_error_html_with_details(self):
        """Test error page with error details."""
        from fastmcp.server.auth.oauth_proxy import create_error_html

        html = create_error_html(
            error_title="OAuth Error",
            error_message="Authentication failed",
            error_details={
                "Error Code": "invalid_scope",
                "Description": "Requested scope does not exist",
            },
        )

        # Verify error details are included
        assert "Error Details" in html
        assert "Error Code" in html
        assert "invalid_scope" in html
        assert "Description" in html
        assert "Requested scope does not exist" in html

    def test_create_error_html_escapes_user_input(self):
        """Test that error page properly escapes HTML in user input."""
        from fastmcp.server.auth.oauth_proxy import create_error_html

        html = create_error_html(
            error_title="Error <script>alert('xss')</script>",
            error_message="Message with <b>HTML</b> tags",
            error_details={"Key<script>": "Value<img>"},
        )

        # Verify HTML is escaped
        assert "<script>alert('xss')</script>" not in html
        assert "&lt;script&gt;" in html
        assert "<b>HTML</b>" not in html
        assert "&lt;b&gt;HTML&lt;/b&gt;" in html

    async def test_callback_error_returns_html_page(self):
        """Test that OAuth callback errors return styled HTML instead of data: URLs."""
        from unittest.mock import Mock

        from starlette.requests import Request
        from starlette.responses import HTMLResponse

        from fastmcp.server.auth.oauth_proxy import OAuthProxy
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        # Create a minimal OAuth proxy
        provider = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=JWTVerifier(
                jwks_uri="https://idp.example.com/.well-known/jwks.json",
                issuer="https://idp.example.com",
                audience="test-client",
            ),
            base_url="http://localhost:8000",
            jwt_signing_key="test-signing-key",
        )

        # Mock a request with an error from the IdP
        mock_request = Mock(spec=Request)
        mock_request.query_params = {
            "error": "invalid_scope",
            "error_description": "The application asked for scope 'read' that doesn't exist",
            "state": "test-state",
        }

        # Call the callback handler
        response = await provider._handle_idp_callback(mock_request)

        # Verify we get an HTMLResponse, not a RedirectResponse
        assert isinstance(response, HTMLResponse)
        assert response.status_code == 400

        # Verify the response contains the error message
        assert b"invalid_scope" in response.body
        assert b"doesn&#x27;t exist" in response.body  # HTML-escaped apostrophe
        assert b"OAuth Error" in response.body


class TestFallbackAccessTokenExpiry:
    """Test fallback access token expiry constants and configuration."""

    def test_default_constants(self):
        """Verify the default expiry constants are set correctly."""
        from fastmcp.server.auth.oauth_proxy import (
            DEFAULT_ACCESS_TOKEN_EXPIRY_NO_REFRESH_SECONDS,
            DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
        )

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
