"""Tests for Descope OAuth provider."""

import os
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from mcp import MCPError
from starlette.requests import Request

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.descope import DescopeProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async

PROJECT_LEVEL_OPENID_CONFIGURATION = {
    "issuer": "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU",
    "scopes_supported": ["mcp:read"],
    "jwks_uri": "https://api.descope.com/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/jwks.json",
}


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

    def test_project_level_config_url_parsing(self):
        """Test project-level well-known URLs without the agentic path segment."""
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/openid-configuration",
            base_url="https://myserver.com",
        )

        assert provider.project_id == "P2v9EBlmO4XTrOwMRfsY1jeUONxU"
        assert str(provider.descope_base_url) == "https://api.descope.com"
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert (
            provider.token_verifier.issuer
            == "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU"
        )
        assert provider.openid_configuration_url == (
            "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/openid-configuration"
        )
        assert provider.oauth_authorization_server_metadata_url == (
            "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/oauth-authorization-server"
        )

    def test_construction_is_network_free(self):
        """Construction must not perform discovery I/O, even when it is enabled."""
        config_url = "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/openid-configuration"

        no_network = AsyncMock(side_effect=AssertionError("no network during init"))
        with patch("httpx2.AsyncClient.get", new=no_network):
            provider = DescopeProvider(
                config_url=config_url,
                base_url="https://myserver.com",
            )

        # Discovery is enabled but deferred; nothing has been fetched yet.
        assert provider._scopes_discovery_enabled is True
        assert provider._scopes_supported is None
        assert provider._discovered_scopes is None
        assert provider._scopes_discovered is False
        assert provider.token_verifier.required_scopes == []

    async def test_discover_scopes_supported_lazily(self):
        """scopes_supported are discovered lazily and cached after first fetch."""
        config_url = "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/openid-configuration"
        provider = DescopeProvider(
            config_url=config_url,
            base_url="https://myserver.com",
        )

        mock_response = httpx2.Response(
            200,
            json=PROJECT_LEVEL_OPENID_CONFIGURATION,
            request=httpx2.Request("GET", config_url),
        )

        with patch(
            "httpx2.AsyncClient.get", new=AsyncMock(return_value=mock_response)
        ) as mock_get:
            scopes = await provider._get_scopes_supported()
            # A second call returns the cached result without another fetch.
            scopes_again = await provider._get_scopes_supported()

        assert scopes == ["mcp:read"]
        assert scopes_again == ["mcp:read"]
        assert provider._discovered_scopes == ["mcp:read"]
        assert provider._scopes_discovered is True
        mock_get.assert_awaited_once()

    async def test_scope_discovery_retries_after_transient_failure(self):
        """A transient discovery failure is not cached and is retried."""
        config_url = "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/openid-configuration"
        provider = DescopeProvider(
            config_url=config_url,
            base_url="https://myserver.com",
        )

        failing = AsyncMock(side_effect=httpx2.ConnectError("boom"))
        with patch("httpx2.AsyncClient.get", new=failing):
            first = await provider._get_scopes_supported()

        # Failure yields no scopes and is not frozen for the provider's lifetime.
        assert first is None
        assert provider._scopes_discovered is False
        assert provider._discovered_scopes is None

        mock_response = httpx2.Response(
            200,
            json=PROJECT_LEVEL_OPENID_CONFIGURATION,
            request=httpx2.Request("GET", config_url),
        )
        with patch("httpx2.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
            second = await provider._get_scopes_supported()

        assert second == ["mcp:read"]
        assert provider._scopes_discovered is True

    async def test_empty_scope_discovery_is_cached_as_success(self):
        """An explicitly empty Descope scope list is a successful discovery."""
        config_url = (
            "https://api.descope.com/v1/apps/P2abc123/.well-known/openid-configuration"
        )
        provider = DescopeProvider(
            config_url=config_url,
            base_url="https://myserver.com",
        )
        mock_response = httpx2.Response(
            200,
            json={"scopes_supported": []},
            request=httpx2.Request("GET", config_url),
        )

        with patch(
            "httpx2.AsyncClient.get", new=AsyncMock(return_value=mock_response)
        ) as mock_get:
            first = await provider._get_scopes_supported()
            second = await provider._get_scopes_supported()

        assert first == []
        assert second == []
        assert provider._scopes_discovered is True
        assert provider._discovered_scopes == []
        mock_get.assert_awaited_once()

    def test_get_routes_is_network_free(self):
        """get_routes must not perform discovery I/O when building routes."""
        config_url = "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/openid-configuration"
        provider = DescopeProvider(
            config_url=config_url,
            base_url="https://myserver.com",
        )

        no_network = AsyncMock(side_effect=AssertionError("no network at get_routes"))
        with patch("httpx2.AsyncClient.get", new=no_network):
            routes = provider.get_routes("/mcp")

        paths = [route.path for route in routes]
        assert any("oauth-protected-resource" in path for path in paths)
        assert any("oauth-authorization-server" in path for path in paths)

    async def test_project_level_metadata_failure_is_not_retried(self):
        """Identical project-level primary and fallback URLs are fetched once."""
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/P2abc123/.well-known/openid-configuration",
            base_url="https://myserver.com",
        )
        metadata_route = next(
            route
            for route in provider.get_routes("/mcp")
            if route.path == "/.well-known/oauth-authorization-server"
        )
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": metadata_route.path,
                "headers": [],
            }
        )

        failing = AsyncMock(side_effect=httpx2.ConnectError("boom"))
        with patch("httpx2.AsyncClient.get", new=failing):
            response = await metadata_route.endpoint(request)

        assert response.status_code == 500
        failing.assert_awaited_once_with(
            provider.oauth_authorization_server_metadata_url
        )

    def test_scopes_supported_and_required_scopes_can_differ(self):
        """Test that scopes_supported and required_scopes can be configured independently."""
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2abc123/M123/.well-known/openid-configuration",
            base_url="https://myserver.com",
            scopes_supported=["mcp:read", "mcp:write"],
            required_scopes=["mcp:read"],
        )

        assert provider._scopes_supported == ["mcp:read", "mcp:write"]
        assert provider.token_verifier.required_scopes == ["mcp:read"]

    def test_explicit_required_scopes_skip_discovery(self):
        """Test that explicit required_scopes disable well-known discovery."""
        config_url = "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/openid-configuration"

        provider = DescopeProvider(
            config_url=config_url,
            base_url="https://myserver.com",
            required_scopes=["custom:scope"],
        )

        assert provider._scopes_discovery_enabled is False
        assert provider.token_verifier.required_scopes == ["custom:scope"]
        assert provider._scopes_supported is None

    def test_explicit_scopes_supported_skip_discovery(self):
        """Test that explicit scopes_supported disable well-known discovery."""
        config_url = "https://api.descope.com/v1/apps/P2v9EBlmO4XTrOwMRfsY1jeUONxU/.well-known/openid-configuration"

        provider = DescopeProvider(
            config_url=config_url,
            base_url="https://myserver.com",
            scopes_supported=["custom:advertised"],
        )

        assert provider._scopes_discovery_enabled is False
        assert provider._scopes_supported == ["custom:advertised"]
        assert provider.token_verifier.required_scopes == []

    def test_custom_token_verifier_scopes_skip_discovery(self):
        """Scopes supplied by a custom verifier retain the parent behavior."""
        token_verifier = JWTVerifier(
            public_key="secret",
            algorithm="HS256",
            required_scopes=["custom:scope"],
        )
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/P2abc123/.well-known/openid-configuration",
            base_url="https://myserver.com",
            token_verifier=token_verifier,
        )

        assert provider._scopes_discovery_enabled is False
        assert provider._scopes_supported is None
        assert provider.token_verifier.scopes_supported == ["custom:scope"]

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
    async def test_protected_resource_metadata_serves_discovered_scopes(self):
        """The protected resource metadata endpoint advertises discovered scopes."""
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2test123/M123/.well-known/openid-configuration",
            base_url="http://localhost:4321",
        )
        mcp = FastMCP(auth=provider)

        with patch(
            "fastmcp.server.auth.providers.descope._discover_scopes",
            new=AsyncMock(return_value=["mcp:read"]),
        ):
            async with run_server_async(mcp, transport="http") as url:
                metadata_url = url.replace(
                    "/mcp", "/.well-known/oauth-protected-resource/mcp"
                )
                async with httpx2.AsyncClient() as client:
                    response = await client.get(metadata_url)

        response.raise_for_status()
        assert response.json()["scopes_supported"] == ["mcp:read"]
        assert response.headers["cache-control"] == "public, max-age=3600"

    async def test_failed_scope_discovery_is_not_cached(self):
        """Clients can retry metadata discovery immediately after a failure."""
        provider = DescopeProvider(
            config_url="https://api.descope.com/v1/apps/agentic/P2test123/M123/.well-known/openid-configuration",
            base_url="http://localhost:4321",
        )
        mcp = FastMCP(auth=provider)

        with patch(
            "fastmcp.server.auth.providers.descope._discover_scopes",
            new=AsyncMock(return_value=None),
        ):
            async with run_server_async(mcp, transport="http") as url:
                metadata_url = url.replace(
                    "/mcp", "/.well-known/oauth-protected-resource/mcp"
                )
                async with httpx2.AsyncClient() as client:
                    response = await client.get(metadata_url)

        response.raise_for_status()
        assert "scopes_supported" not in response.json()
        assert response.headers["cache-control"] == "no-store"

    async def test_unauthorized_access(self, mcp_server_url: str):
        # SDK v2 surfaces the server's 401 as a generic MCPError at the client
        # boundary rather than re-raising httpx2.HTTPStatusError.
        with pytest.raises(MCPError):
            async with Client(mcp_server_url) as client:
                tools = await client.list_tools()  # noqa: F841
        assert "tools" not in locals()

    # async def test_authorized_access(self, client_with_headless_oauth: Client):
    #     async with client_with_headless_oauth:
    #         tools = await client_with_headless_oauth.list_tools()
    #     assert tools is not None
    #     assert len(tools) > 0
    #     assert "add" in tools
