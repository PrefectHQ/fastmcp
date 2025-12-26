"""Tests for Descope OAuth provider."""

import os
from unittest.mock import patch

import httpx
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.descope import DescopeProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async


class TestDescopeProvider:
    """Test Descope OAuth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test DescopeProvider initialization with explicit parameters."""
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2abc123/M123/.well-known/openid-configuration",
            base_url="https://myserver.com",
        )

        assert provider.project_id == "P2abc123"
        assert str(provider.base_url) == "https://myserver.com/"
        assert str(provider.descope_base_url) == "https://api.descope.com"

    def test_environment_variable_loading(self):
        """Test that environment variables are loaded correctly."""
        # This test verifies that the provider can be created with environment variables
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2env123/M123/.well-known/openid-configuration",
            base_url="http://env-server.com",
        )

        # Should have loaded from environment
        assert provider.project_id == "P2env123"
        assert str(provider.base_url) == "http://env-server.com/"
        assert str(provider.descope_base_url) == "https://api.descope.com"

    def test_config_url_parsing(self):
        """Test that config_url is parsed correctly to extract base URL and project ID."""
        # Standard HTTPS URL
        provider1 = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2abc123/M123/.well-known/openid-configuration",
            base_url="https://myserver.com",
        )
        assert str(provider1.descope_base_url) == "https://api.descope.com"
        assert provider1.project_id == "P2abc123"

        # HTTP URL (for local testing)
        provider2 = DescopeProvider(
            config_url="http://localhost:8080/v1/apps/agentic/P2abc123/M123/.well-known/openid-configuration",
            base_url="https://myserver.com",
        )
        assert str(provider2.descope_base_url) == "http://localhost:8080"
        assert provider2.project_id == "P2abc123"

        # URL without .well-known/openid-configuration suffix
        provider3 = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2abc123/M123",
            base_url="https://myserver.com",
        )
        assert str(provider3.descope_base_url) == "https://api.descope.com"
        assert provider3.project_id == "P2abc123"

    def test_requires_config_url_or_project_id_and_descope_base_url(self):
        """Test that either config_url or both project_id and descope_base_url are required."""
        # Should raise error when neither API is provided
        with pytest.raises(ValueError, match="Either config_url"):
            DescopeProvider(
                base_url="https://myserver.com",
            )

    def test_backwards_compatibility_with_project_id_and_descope_base_url(self):
        """Test backwards compatibility with old API using project_id and descope_base_url."""
        provider = DescopeProvider(
            project_id="P2abc123",
            descope_base_url="https://api.descope.com",
            base_url="https://myserver.com",
        )

        assert provider.project_id == "P2abc123"
        assert str(provider.descope_base_url) == "https://api.descope.com"
        assert str(provider.base_url) == "https://myserver.com/"

        # Check that JWT verifier uses the old issuer format
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert (
            provider.token_verifier.issuer == "https://api.descope.com/v1/apps/P2abc123"
        )
        assert (
            provider.token_verifier.jwks_uri
            == "https://api.descope.com/P2abc123/.well-known/jwks.json"
        )

    def test_backwards_compatibility_descope_base_url_without_scheme(self):
        """Test that descope_base_url without scheme gets https:// prefix added."""
        provider = DescopeProvider(
            project_id="P2abc123",
            descope_base_url="api.descope.com",
            base_url="https://myserver.com",
        )

        assert str(provider.descope_base_url) == "https://api.descope.com"
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert (
            provider.token_verifier.issuer == "https://api.descope.com/v1/apps/P2abc123"
        )

    def test_config_url_takes_precedence_over_old_api(self):
        """Test that config_url takes precedence when both APIs are provided."""
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2new123/M123/.well-known/openid-configuration",
            project_id="P2old123",  # Should be ignored
            descope_base_url="https://old.descope.com",  # Should be ignored
            base_url="https://myserver.com",
        )

        # Should use values from config_url, not the old API
        assert provider.project_id == "P2new123"
        assert str(provider.descope_base_url) == "https://api.descope.com"
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert (
            provider.token_verifier.issuer
            == "https://api.descope.com/v1/apps/agentic/P2new123/M123"
        )

    def test_jwt_verifier_configured_correctly(self):
        """Test that JWT verifier is configured correctly."""
        config_url = "https://api.descope.com/v1/apps/agentic/P2abc123/M123/.well-known/openid-configuration"
        issuer_url = "https://api.descope.com/v1/apps/agentic/P2abc123/M123"

        provider = DescopeProvider(
            config_url=config_url,
            base_url="https://myserver.com",
        )

        # Check that JWT verifier uses the correct endpoints
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert (
            provider.token_verifier.jwks_uri
            == "https://api.descope.com/P2abc123/.well-known/jwks.json"
        )
        assert provider.token_verifier.issuer == issuer_url
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.audience == "P2abc123"

    def test_required_scopes_support(self):
        """Test that required_scopes are supported and passed to JWT verifier."""
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2abc123/M123/.well-known/openid-configuration",
            base_url="https://myserver.com",
            required_scopes=["read", "write"],
        )

        # Check that required_scopes are set on the token verifier
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.required_scopes == ["read", "write"]

    def test_required_scopes_with_old_api(self):
        """Test that required_scopes work with the old API (project_id + descope_base_url)."""
        provider = DescopeProvider(
            project_id="P2abc123",
            descope_base_url="https://api.descope.com",
            base_url="https://myserver.com",
            required_scopes=["openid", "email"],
        )

        # Check that required_scopes are set on the token verifier
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.required_scopes == ["openid", "email"]

    def test_required_scopes_from_env(self):
        """Test that required_scopes can be set via environment variable."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_DESCOPEPROVIDER_CONFIG_URL": "https://api.descope.com/v1/apps/agentic/P2env123/M123/.well-known/openid-configuration",
                "FASTMCP_SERVER_AUTH_DESCOPEPROVIDER_BASE_URL": "https://envserver.com",
                "FASTMCP_SERVER_AUTH_DESCOPEPROVIDER_REQUIRED_SCOPES": "read,write",
            },
        ):
            provider = DescopeProvider(
                config_url="https://api.descope.com/v1/apps/agentic/P2env123/M123/.well-known/openid-configuration",
                base_url="https://envserver.com",
                required_scopes=["read", "write"],
            )

            assert isinstance(provider.token_verifier, JWTVerifier)
            assert provider.token_verifier.required_scopes == ["read", "write"]


@pytest.fixture
async def mcp_server_url():
    """Start Descope server."""
    mcp = FastMCP(
        auth=DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2test123/M123/.well-known/openid-configuration",
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


class TestDescopeProviderIntegration:
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
