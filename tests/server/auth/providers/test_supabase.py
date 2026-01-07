"""Tests for Supabase Auth provider."""

from collections.abc import Generator

import httpx
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.supabase import SupabaseProvider
from fastmcp.utilities.tests import HeadlessOAuth, run_server_in_process


class TestSupabaseProvider:
    """Test Supabase Auth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test SupabaseProvider initialization with explicit parameters."""
        provider = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://myserver.com",
        )

        assert provider.project_url == "https://abc123.supabase.co"
        assert str(provider.base_url) == "https://myserver.com/"

    def test_environment_variable_loading(self):
        """Test that environment variables are loaded correctly."""
        provider = SupabaseProvider(
            project_url="https://env123.supabase.co",
            base_url="http://env-server.com",
        )

        assert provider.project_url == "https://env123.supabase.co"
        assert str(provider.base_url) == "http://env-server.com/"

    def test_project_url_normalization(self):
        """Test that project_url handles trailing slashes correctly."""
        # Without trailing slash
        provider1 = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://myserver.com",
        )
        assert provider1.project_url == "https://abc123.supabase.co"

        # With trailing slash - should be stripped
        provider2 = SupabaseProvider(
            project_url="https://abc123.supabase.co/",
            base_url="https://myserver.com",
        )
        assert provider2.project_url == "https://abc123.supabase.co"

    def test_jwt_verifier_configured_correctly(self):
        """Test that JWT verifier is configured correctly."""
        provider = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://myserver.com",
        )

        # Check that JWT verifier uses the correct endpoints (default auth_route)
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert (
            provider.token_verifier.jwks_uri
            == "https://abc123.supabase.co/auth/v1/.well-known/jwks.json"
        )
        assert provider.token_verifier.issuer == "https://abc123.supabase.co/auth/v1"
        assert provider.token_verifier.algorithm == "ES256"

    def test_jwt_verifier_with_required_scopes(self):
        """Test that JWT verifier respects required_scopes."""
        provider = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://myserver.com",
            required_scopes=["openid", "email"],
        )

        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.required_scopes == ["openid", "email"]

    def test_authorization_servers_configured(self):
        """Test that authorization servers list is configured correctly."""
        provider = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://myserver.com",
        )

        assert len(provider.authorization_servers) == 1
        assert (
            str(provider.authorization_servers[0])
            == "https://abc123.supabase.co/auth/v1"
        )

    @pytest.mark.parametrize(
        "algorithm",
        ["HS256", "RS256", "ES256"],
    )
    def test_algorithm_configuration(self, algorithm):
        """Test that algorithm can be configured for different JWT signing methods."""
        provider = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://myserver.com",
            algorithm=algorithm,
        )

        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.algorithm == algorithm

    def test_algorithm_default_es256(self):
        """Test that algorithm defaults to ES256 when not specified."""
        provider = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://myserver.com",
        )

        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.algorithm == "ES256"

    def test_algorithm_from_parameter(self):
        """Test that algorithm can be configured via parameter."""
        provider = SupabaseProvider(
            project_url="https://env123.supabase.co",
            base_url="https://envserver.com",
            algorithm="RS256",
        )

        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.algorithm == "RS256"

    def test_custom_auth_route(self):
        provider = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://myserver.com",
            auth_route="/custom/auth/route",
        )

        assert provider.auth_route == "custom/auth/route"
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert (
            provider.token_verifier.jwks_uri
            == "https://abc123.supabase.co/custom/auth/route/.well-known/jwks.json"
        )

    def test_custom_auth_route_trailing_slash(self):
        provider = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://myserver.com",
            auth_route="/custom/auth/route/",
        )

        assert provider.auth_route == "custom/auth/route"


def run_mcp_server(host: str, port: int) -> None:
    mcp = FastMCP(
        auth=SupabaseProvider(
            project_url="https://test123.supabase.co",
            base_url="http://localhost:4321",
        )
    )

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    mcp.run(host=host, port=port, transport="http")


@pytest.fixture
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


class TestSupabaseProviderIntegration:
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
