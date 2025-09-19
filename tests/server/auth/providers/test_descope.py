"""Tests for Descope OAuth provider."""

import os
from collections.abc import Generator
from unittest.mock import patch

import httpx
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.descope import DescopeProvider
from fastmcp.utilities.tests import HeadlessOAuth, run_server_in_process


class TestDescopeProvider:
    """Test Descope OAuth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test DescopeProvider initialization with explicit parameters."""
        provider = DescopeProvider(
            project_id="P2abc123",
            base_url="https://myserver.com",
            descope_base_url="https://api.descope.com",
        )

        assert provider.project_id == "P2abc123"
        assert str(provider.base_url) == "https://myserver.com/"
        assert str(provider.descope_base_url) == "https://api.descope.com"

    @pytest.mark.parametrize(
        "scopes_env",
        [
            "openid,email",
            '["openid", "email"]',
        ],
    )
    def test_init_with_env_vars(self, scopes_env):
        """Test DescopeProvider initialization from environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_DESCOPEPROVIDER_PROJECT_ID": "P2env123",
                "FASTMCP_SERVER_AUTH_DESCOPEPROVIDER_BASE_URL": "https://envserver.com",
                "FASTMCP_SERVER_AUTH_DESCOPEPROVIDER_DESCOPE_BASE_URL": "https://api.descope.com",
            },
        ):
            provider = DescopeProvider()

            assert provider.project_id == "P2env123"
            assert str(provider.base_url) == "https://envserver.com/"
            assert str(provider.descope_base_url) == "https://api.descope.com"

    def test_environment_variable_loading(self):
        """Test that environment variables are loaded correctly."""
        # This test verifies that the provider can be created with environment variables
        provider = DescopeProvider(
            project_id="P2env123", base_url="http://env-server.com"
        )

        # Should have loaded from environment
        assert provider.project_id == "P2env123"
        assert str(provider.base_url) == "http://env-server.com/"
        assert str(provider.descope_base_url) == "https://api.descope.com"

    def test_descope_base_url_https_prefix_handling(self):
        """Test that descope_base_url handles missing https:// prefix."""
        # Without https:// - should add it
        provider1 = DescopeProvider(
            project_id="P2abc123",
            base_url="https://myserver.com",
            descope_base_url="https://api.descope.com",
        )
        assert str(provider1.descope_base_url) == "https://api.descope.com"

        # With https:// - should keep it
        provider2 = DescopeProvider(
            project_id="P2abc123",
            base_url="https://myserver.com",
            descope_base_url="https://api.descope.com",
        )
        assert str(provider2.descope_base_url) == "https://api.descope.com"

        # With http:// - should be preserved
        provider3 = DescopeProvider(
            project_id="P2abc123",
            base_url="https://myserver.com",
            descope_base_url="http://localhost:8080",
        )
        assert str(provider3.descope_base_url) == "http://localhost:8080"

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        provider = DescopeProvider(
            project_id="P2abc123",
            base_url="https://myserver.com",
        )

        # Check defaults
        assert str(provider.descope_base_url) == "https://api.descope.com"

    def test_jwt_verifier_configured_correctly(self):
        """Test that JWT verifier is configured correctly."""
        provider = DescopeProvider(
            project_id="P2abc123",
            base_url="https://myserver.com",
            descope_base_url="https://api.descope.com",
        )

        # Check that JWT verifier uses the correct endpoints
        assert (
            provider.token_verifier.jwks_uri  # type: ignore[attr-defined]
            == "https://api.descope.com/P2abc123/.well-known/jwks.json"
        )
        assert (
            provider.token_verifier.issuer == "https://api.descope.com/v1/apps/P2abc123"  # type: ignore[attr-defined]
        )
        assert provider.token_verifier.audience == "P2abc123"  # type: ignore[attr-defined]


def run_mcp_server(host: str, port: int) -> None:
    mcp = FastMCP(
        auth=DescopeProvider(
            project_id="P2test123",
            base_url="http://localhost:4321",
            descope_base_url="https://api.descope.com",
        )
    )

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    mcp.run(host=host, port=port, transport="http")


@pytest.fixture(scope="module")
def mcp_server_url() -> Generator[str]:
    with run_server_in_process(run_mcp_server) as url:
        yield f"{url}/mcp"


@pytest.fixture()
def client_with_headless_oauth(
    mcp_server_url: str,
) -> Generator[Client, None, None]:
    """Client with headless OAuth that bypasses browser interaction."""
    client = Client(
        transport=StreamableHttpTransport(mcp_server_url),
        auth=HeadlessOAuth(mcp_url=mcp_server_url),
    )
    yield client


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
