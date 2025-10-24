"""Tests for WorkOS OAuth provider."""

import os
from unittest.mock import patch
from urllib.parse import urlparse

import httpx
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.workos import AuthKitProvider, WorkOSProvider
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async


class TestWorkOSProvider:
    """Test WorkOS OAuth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test WorkOSProvider initialization with explicit parameters."""
        provider = WorkOSProvider(
            client_id="client_test123",
            client_secret="secret_test456",
            authkit_domain="https://test.authkit.app",
            base_url="https://myserver.com",
            required_scopes=["openid", "profile"],
            jwt_signing_key="test-secret",
        )

        assert provider._upstream_client_id == "client_test123"
        assert provider._upstream_client_secret.get_secret_value() == "secret_test456"
        assert str(provider.base_url) == "https://myserver.com/"

    @pytest.mark.parametrize(
        "scopes_env",
        [
            "openid,email",
            '["openid", "email"]',
        ],
    )
    def test_init_with_env_vars(self, scopes_env):
        """Test WorkOSProvider initialization from environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_WORKOS_CLIENT_ID": "env_client",
                "FASTMCP_SERVER_AUTH_WORKOS_CLIENT_SECRET": "env_secret",
                "FASTMCP_SERVER_AUTH_WORKOS_AUTHKIT_DOMAIN": "https://env.authkit.app",
                "FASTMCP_SERVER_AUTH_WORKOS_BASE_URL": "https://envserver.com",
                "FASTMCP_SERVER_AUTH_WORKOS_REQUIRED_SCOPES": scopes_env,
                "FASTMCP_SERVER_AUTH_WORKOS_JWT_SIGNING_KEY": "test-secret",
            },
        ):
            provider = WorkOSProvider()

            assert provider._upstream_client_id == "env_client"
            assert provider._upstream_client_secret.get_secret_value() == "env_secret"
            assert str(provider.base_url) == "https://envserver.com/"
            assert provider._token_validator.required_scopes == [
                "openid",
                "email",
            ]

    def test_init_missing_client_id_raises_error(self):
        """Test that missing client_id raises ValueError."""
        with pytest.raises(ValueError, match="client_id is required"):
            WorkOSProvider(
                client_secret="test_secret",
                authkit_domain="https://test.authkit.app",
            )

    def test_init_missing_client_secret_raises_error(self):
        """Test that missing client_secret raises ValueError."""
        with pytest.raises(ValueError, match="client_secret is required"):
            WorkOSProvider(
                client_id="test_client",
                authkit_domain="https://test.authkit.app",
            )

    def test_init_missing_authkit_domain_raises_error(self):
        """Test that missing authkit_domain raises ValueError."""
        with pytest.raises(ValueError, match="authkit_domain is required"):
            WorkOSProvider(
                client_id="test_client",
                client_secret="test_secret",
            )

    def test_authkit_domain_https_prefix_handling(self):
        """Test that authkit_domain handles missing https:// prefix."""
        # Without https:// - should add it
        provider1 = WorkOSProvider(
            client_id="test_client",
            client_secret="test_secret",
            authkit_domain="test.authkit.app",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )
        parsed = urlparse(provider1._upstream_authorization_endpoint)
        assert parsed.scheme == "https"
        assert parsed.netloc == "test.authkit.app"
        assert parsed.path == "/oauth2/authorize"

        # With https:// - should keep it
        provider2 = WorkOSProvider(
            client_id="test_client",
            client_secret="test_secret",
            authkit_domain="https://test.authkit.app",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )
        parsed = urlparse(provider2._upstream_authorization_endpoint)
        assert parsed.scheme == "https"
        assert parsed.netloc == "test.authkit.app"
        assert parsed.path == "/oauth2/authorize"

        # With http:// - should be preserved
        provider3 = WorkOSProvider(
            client_id="test_client",
            client_secret="test_secret",
            authkit_domain="http://localhost:8080",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )
        parsed = urlparse(provider3._upstream_authorization_endpoint)
        assert parsed.scheme == "http"
        assert parsed.netloc == "localhost:8080"
        assert parsed.path == "/oauth2/authorize"

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        provider = WorkOSProvider(
            client_id="test_client",
            client_secret="test_secret",
            authkit_domain="https://test.authkit.app",
            jwt_signing_key="test-secret",
        )

        # Check defaults
        assert provider.base_url is None
        assert provider._redirect_path == "/auth/callback"
        # WorkOS provider has no default scopes but we can't easily verify without accessing internals

    def test_oauth_endpoints_configured_correctly(self):
        """Test that OAuth endpoints are configured correctly."""
        provider = WorkOSProvider(
            client_id="test_client",
            client_secret="test_secret",
            authkit_domain="https://test.authkit.app",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )

        # Check that endpoints use the authkit domain
        assert (
            provider._upstream_authorization_endpoint
            == "https://test.authkit.app/oauth2/authorize"
        )
        assert (
            provider._upstream_token_endpoint == "https://test.authkit.app/oauth2/token"
        )
        assert (
            provider._upstream_revocation_endpoint is None
        )  # WorkOS doesn't support revocation


@pytest.fixture
async def mcp_server_url():
    """Start AuthKit server."""
    mcp = FastMCP(
        auth=AuthKitProvider(
            authkit_domain="https://respectful-lullaby-34-staging.authkit.app",
            base_url="http://localhost:4321",
        )
    )

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    async with run_server_async(mcp, transport="http") as url:
        yield url


@pytest.fixture
def client_with_headless_oauth(mcp_server_url: str) -> Client:
    """Client with headless OAuth that bypasses browser interaction."""
    return Client(
        transport=StreamableHttpTransport(mcp_server_url),
        auth=HeadlessOAuth(mcp_url=mcp_server_url),
    )


class TestAuthKitProvider:
    async def test_unauthorized_access(self, mcp_server_url: str):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url) as client:
                tools = await client.list_tools()  # noqa: F841

        assert isinstance(exc_info.value, httpx.HTTPStatusError)
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    # async def test_authorized_access(self, client_with_headless_oauth: Client):
    #     async with client_with_headless_oauth:
    #         tools = await client_with_headless_oauth.list_tools()
    #     assert tools is not None
    #     assert len(tools) > 0
    #     assert "add" in tools
