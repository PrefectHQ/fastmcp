"""Integration tests for GitHub OAuth Provider.

Tests the complete GitHub OAuth flow using HeadlessOAuth to bypass browser interaction.

This test requires a GitHub OAuth app to be created at https://github.com/settings/developers
with the following configuration:
- Redirect URL: http://127.0.0.1:9100/auth/callback
- Client ID and Client Secret should be set as environment variables:
  - FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID
  - FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET
"""

import os
import re
import secrets
import time
from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy.models import ClientCode
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async

FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID = os.getenv("FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID")
FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET = os.getenv(
    "FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET"
)

# Skip tests if no GitHub OAuth credentials are available
pytestmark = pytest.mark.xfail(
    not FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID
    or not FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET,
    reason="FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID and FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET environment variables are not set or empty",
)


def create_github_server(base_url: str) -> FastMCP:
    """Create FastMCP server with GitHub OAuth protection."""
    assert FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID is not None
    assert FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET is not None

    # Create GitHub OAuth provider
    auth = GitHubProvider(
        client_id=FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID,
        client_secret=FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET,
        base_url=base_url,
        jwt_signing_key="test-secret",
    )

    # Create FastMCP server with GitHub authentication
    server = FastMCP("GitHub OAuth Integration Test Server", auth=auth)

    @server.tool
    def get_protected_data() -> str:
        """Returns protected data - requires GitHub OAuth."""
        return "🔐 This data requires GitHub OAuth authentication!"

    @server.tool
    def get_user_info() -> str:
        """Returns user info from OAuth context."""
        return "📝 GitHub OAuth user authenticated successfully"

    return server


def create_github_server_with_mock_callback(base_url: str) -> FastMCP:
    """Create FastMCP server with GitHub OAuth that mocks the callback for testing."""
    assert FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID is not None
    assert FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET is not None

    # Create GitHub OAuth provider
    auth = GitHubProvider(
        client_id=FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID,
        client_secret=FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET,
        base_url=base_url,
        jwt_signing_key="test-secret",
    )

    # Mock the authorize method to return a fake code instead of redirecting to GitHub
    async def mock_authorize(client, params):
        # Instead of redirecting to GitHub, simulate an immediate callback
        # Generate a fake authorization code
        fake_code = secrets.token_urlsafe(32)

        # Create mock token response (simulating what GitHub would return)
        mock_tokens = {
            "access_token": f"gho_mock_token_{secrets.token_hex(16)}",
            "token_type": "bearer",
            "expires_in": 3600,
        }

        # Store the mock tokens in the proxy's code storage
        await auth._code_store.put(
            key=fake_code,
            value=ClientCode(
                code=fake_code,
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                code_challenge=params.code_challenge,
                code_challenge_method=getattr(params, "code_challenge_method", "S256"),
                scopes=params.scopes or [],
                idp_tokens=mock_tokens,
                expires_at=int(time.time() + 300),  # 5 minutes
                created_at=time.time(),
            ),
        )

        # Return the redirect to the client's callback with the fake code
        callback_params = {
            "code": fake_code,
            "state": params.state,
        }
        separator = "&" if "?" in str(params.redirect_uri) else "?"
        return f"{params.redirect_uri}{separator}{urlencode(callback_params)}"

    auth.authorize = mock_authorize  # type: ignore[assignment]

    # Mock the token verifier to accept our fake tokens
    original_verify_token = auth._token_validator.verify_token

    async def mock_verify_token(token: str):
        if token.startswith("gho_mock_token_"):
            # Return a mock AccessToken for our fake tokens
            return AccessToken(
                token=token,
                client_id=FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID or "test-client",
                scopes=["user"],
                expires_at=int(time.time() + 3600),
            )
        # Fall back to original verification for other tokens
        return await original_verify_token(token)

    auth._token_validator.verify_token = mock_verify_token  # type: ignore[assignment]

    # Create FastMCP server with mocked GitHub authentication
    server = FastMCP("GitHub OAuth Integration Test Server (Mock)", auth=auth)

    @server.tool
    def get_protected_data() -> str:
        """Returns protected data - requires GitHub OAuth."""
        return "🔐 This data requires GitHub OAuth authentication!"

    @server.tool
    def get_user_info() -> str:
        """Returns user info from OAuth context."""
        return "📝 GitHub OAuth user authenticated successfully"

    return server


@pytest.fixture
async def github_server() -> AsyncGenerator[str, None]:
    """Start GitHub OAuth server on a random available port."""
    from fastmcp.utilities.http import find_available_port

    port = find_available_port()
    base_url = f"http://127.0.0.1:{port}"
    server = create_github_server(base_url)
    async with run_server_async(server, port=port, transport="http") as url:
        yield url


@pytest.fixture
async def github_server_with_mock() -> AsyncGenerator[str, None]:
    """Start GitHub OAuth server with mocked callback on a random available port."""
    from fastmcp.utilities.http import find_available_port

    port = find_available_port()
    base_url = f"http://127.0.0.1:{port}"
    server = create_github_server_with_mock_callback(base_url)
    async with run_server_async(server, port=port, transport="http") as url:
        yield url


@pytest.fixture
def github_client(github_server: str) -> Client:
    """Create FastMCP client with HeadlessOAuth for GitHub server."""
    return Client(
        github_server,
        auth=HeadlessOAuth(mcp_url=github_server),
    )


@pytest.fixture
def github_client_with_mock(github_server_with_mock: str) -> Client:
    """Create FastMCP client with HeadlessOAuth for mocked GitHub server."""
    return Client(
        github_server_with_mock,
        auth=HeadlessOAuth(mcp_url=github_server_with_mock),
    )


async def test_github_oauth_credentials_available():
    """Test that GitHub OAuth credentials are available for testing."""
    assert FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID is not None
    assert FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET is not None
    assert len(FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID) > 0
    assert len(FASTMCP_TEST_AUTH_GITHUB_CLIENT_SECRET) > 0


async def test_github_oauth_authorization_redirect(github_server: str):
    """Test that GitHub OAuth authorization redirects to GitHub correctly through consent flow.

    Since HeadlessOAuth can't handle real GitHub redirects, we test that:
    1. DCR client registration works
    2. Authorization endpoint redirects to consent page
    3. Consent approval redirects to GitHub with correct parameters
    """
    # Extract base URL
    parsed = urlparse(github_server)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    async with httpx.AsyncClient() as http_client:
        # Step 1: Register OAuth client (DCR)
        register_response = await http_client.post(
            f"{base_url}/register",
            json={
                "client_name": "Integration Test Client",
                "redirect_uris": ["http://localhost:12345/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
        if register_response.status_code != 201:
            print(f"Registration failed: {register_response.status_code}")
            print(f"Response: {register_response.text}")
        assert register_response.status_code == 201

        client_info = register_response.json()
        client_id = client_info["client_id"]
        assert client_id is not None

        # Step 2: Test authorization endpoint redirects to consent page
        auth_url = f"{base_url}/authorize"
        auth_params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost:12345/callback",
            "state": "test-state-123",
            "code_challenge": "test-challenge",
            "code_challenge_method": "S256",
        }

        auth_response = await http_client.get(
            auth_url, params=auth_params, follow_redirects=False
        )

        # Should redirect to consent page (confused deputy protection)
        assert auth_response.status_code == 302
        consent_location = auth_response.headers["location"]
        assert "/consent" in consent_location

        # Step 3: Visit consent page to get CSRF token
        consent_response = await http_client.get(
            consent_location, follow_redirects=False
        )
        assert consent_response.status_code == 200

        # Extract CSRF token from consent page HTML
        csrf_match = re.search(
            r'name="csrf_token"\s+value="([^"]+)"', consent_response.text
        )
        assert csrf_match, "CSRF token not found in consent page"
        csrf_token = csrf_match.group(1)

        # Extract txn_id from consent URL
        txn_id_match = re.search(r"txn_id=([^&]+)", consent_location)
        assert txn_id_match, "txn_id not found in consent URL"
        txn_id = txn_id_match.group(1)

        # Step 4: Approve consent
        approve_response = await http_client.post(
            f"{base_url}/consent",
            data={
                "action": "approve",
                "txn_id": txn_id,
                "csrf_token": csrf_token,
            },
            cookies=consent_response.cookies,
            follow_redirects=False,
        )

        # Should redirect to GitHub
        assert approve_response.status_code in (302, 303)
        redirect_location = approve_response.headers["location"]

        # Parse redirect URL - should be GitHub
        redirect_parsed = urlparse(redirect_location)
        assert redirect_parsed.hostname == "github.com"
        assert redirect_parsed.path == "/login/oauth/authorize"

        # Check that GitHub gets the right parameters
        github_params = parse_qs(redirect_parsed.query)
        assert "client_id" in github_params
        assert github_params["client_id"][0] == FASTMCP_TEST_AUTH_GITHUB_CLIENT_ID
        assert "redirect_uri" in github_params
        # The redirect_uri should be our proxy's callback, not the client's
        proxy_callback = github_params["redirect_uri"][0]
        assert proxy_callback.startswith(base_url)
        assert proxy_callback.endswith("/auth/callback")


async def test_github_oauth_server_metadata(github_server: str):
    """Test OAuth server metadata discovery."""
    from urllib.parse import urlparse

    import httpx

    # Extract base URL from server URL
    parsed = urlparse(github_server)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    async with httpx.AsyncClient() as http_client:
        # Test OAuth authorization server metadata
        metadata_response = await http_client.get(
            f"{base_url}/.well-known/oauth-authorization-server"
        )
        assert metadata_response.status_code == 200

        metadata = metadata_response.json()
        assert "authorization_endpoint" in metadata
        assert "token_endpoint" in metadata
        assert "registration_endpoint" in metadata
        assert "issuer" in metadata

        # Verify endpoints are properly formed
        assert metadata["authorization_endpoint"].startswith(base_url)
        assert metadata["token_endpoint"].startswith(base_url)
        assert metadata["registration_endpoint"].startswith(base_url)


async def test_github_oauth_unauthorized_access(github_server: str):
    """Test that unauthenticated requests are rejected."""
    import httpx

    from fastmcp.client.transports import StreamableHttpTransport

    # Create client without OAuth authentication
    unauthorized_client = Client(transport=StreamableHttpTransport(github_server))

    # Attempt to connect without authentication should fail
    with pytest.raises(httpx.HTTPStatusError, match="401 Unauthorized"):
        async with unauthorized_client:
            pass


async def test_github_oauth_with_mock(github_client_with_mock: Client):
    """Test complete GitHub OAuth flow with mocked callback."""

    async with github_client_with_mock:
        # Test that we can ping the server (requires successful OAuth)
        assert await github_client_with_mock.ping()

        # Test that we can call protected tools
        result = await github_client_with_mock.call_tool("get_protected_data", {})
        assert "🔐 This data requires GitHub OAuth authentication!" in str(result.data)

        # Test that we can call user info tool
        result = await github_client_with_mock.call_tool("get_user_info", {})
        assert "📝 GitHub OAuth user authenticated successfully" in str(result.data)


async def test_github_oauth_mock_only_accepts_mock_tokens(github_server_with_mock: str):
    """Test that the mock token verifier only accepts mock tokens, not real ones."""
    from urllib.parse import urlparse

    import httpx

    # Extract base URL
    parsed = urlparse(github_server_with_mock)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    async with httpx.AsyncClient() as http_client:
        # Test that a fake "real" GitHub token is rejected
        fake_real_token = "gho_real_token_should_be_rejected"

        auth_response = await http_client.post(
            f"{base_url}/mcp",
            headers={
                "Authorization": f"Bearer {fake_real_token}",
                "Content-Type": "application/json",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )

        # Should be unauthorized because it's not a mock token
        assert auth_response.status_code == 401
