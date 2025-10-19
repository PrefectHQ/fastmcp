"""Tests for Scalekit OAuth provider."""

import os
from unittest.mock import patch

import httpx
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.scalekit import ScalekitProvider
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async


class TestScalekitProvider:
    """Test Scalekit OAuth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test ScalekitProvider initialization with explicit parameters."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com",
            client_id="sk_client_123",
            resource_id="sk_resource_456",
            mcp_url="https://myserver.com/",
        )

        assert provider.environment_url == "https://my-env.scalekit.com"
        assert provider.client_id == "sk_client_123"
        assert provider.resource_id == "sk_resource_456"
        assert str(provider.mcp_url) == "https://myserver.com/"

    def test_init_with_env_vars(self):
        """Test ScalekitProvider initialization from environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_SCALEKITPROVIDER_ENVIRONMENT_URL": "https://env-scalekit.com",
                "FASTMCP_SERVER_AUTH_SCALEKITPROVIDER_CLIENT_ID": "skc_123",
                "FASTMCP_SERVER_AUTH_SCALEKITPROVIDER_RESOURCE_ID": "res_456",
                "FASTMCP_SERVER_AUTH_SCALEKITPROVIDER_MCP_URL": "https://envserver.com/mcp",
            },
        ):
            provider = ScalekitProvider()

            assert provider.environment_url == "https://env-scalekit.com"
            assert provider.client_id == "skc_123"
            assert provider.resource_id == "res_456"
            assert str(provider.mcp_url) == "https://envserver.com/mcp"

    def test_environment_variable_loading(self):
        """Test that environment variables are loaded correctly."""
        provider = ScalekitProvider(
            environment_url="https://test-env.scalekit.com",
            client_id="sk_client_test_123",
            resource_id="sk_resource_test_456",
            mcp_url="http://test-server.com",
        )

        assert provider.environment_url == "https://test-env.scalekit.com"
        assert provider.client_id == "sk_client_test_123"
        assert provider.resource_id == "sk_resource_test_456"
        assert str(provider.mcp_url) == "http://test-server.com/"

    def test_url_trailing_slash_handling(self):
        """Test that URLs handle trailing slashes correctly."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com/",
            client_id="sk_client_123",
            resource_id="sk_resource_456",
            mcp_url="https://myserver.com/",
        )

        assert provider.environment_url == "https://my-env.scalekit.com"
        assert str(provider.mcp_url) == "https://myserver.com/"

    def test_jwt_verifier_configured_correctly(self):
        """Test that JWT verifier is configured correctly."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com",
            client_id="sk_client_123",
            resource_id="sk_resource_456",
            mcp_url="https://myserver.com/",
        )

        # Check that JWT verifier uses the correct endpoints
        assert (
            provider.token_verifier.jwks_uri  # type: ignore[attr-defined]
            == "https://my-env.scalekit.com/keys"
        )
        assert (
            provider.token_verifier.issuer == "https://my-env.scalekit.com"  # type: ignore[attr-defined]
        )
        assert provider.token_verifier.audience == "https://myserver.com/"  # type: ignore[attr-defined]

    def test_authorization_servers_configuration(self):
        """Test that authorization servers are configured correctly."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com",
            client_id="sk_client_123",
            resource_id="sk_resource_456",
            mcp_url="https://myserver.com/",
        )

        assert len(provider.authorization_servers) == 1
        assert (
            str(provider.authorization_servers[0])
            == "https://my-env.scalekit.com/resources/sk_resource_456"
        )


@pytest.fixture
async def mcp_server_url():
    """Start Scalekit server."""
    mcp = FastMCP(
        auth=ScalekitProvider(
            environment_url="https://test-env.scalekit.com",
            client_id="sk_client_test_123",
            resource_id="sk_resource_test_456",
            mcp_url="http://localhost:4321",
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


class TestScalekitProviderIntegration:
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
