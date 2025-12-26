"""Tests for Scalekit OAuth provider."""

import httpx
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.scalekit import ScalekitProvider
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async


class TestScalekitProvider:
    """Test Scalekit OAuth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test ScalekitProvider initialization with explicit parameters."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com",
            resource_id="sk_resource_456",
            base_url="https://myserver.com/",
            required_scopes=["read"],
        )

        assert provider.environment_url == "https://my-env.scalekit.com"
        assert provider.resource_id == "sk_resource_456"
        assert str(provider.base_url) == "https://myserver.com/"
        assert provider.required_scopes == ["read"]

    def test_init_with_mcp_url_only(self):
        """Allow legacy mcp_url parameter as base_url."""
        provider = ScalekitProvider(
            environment_url="https://legacy.scalekit.com",
            resource_id="sk_resource_legacy",
            mcp_url="https://legacy-app.com/",
        )

        assert str(provider.base_url) == "https://legacy-app.com/"

    def test_init_prefers_base_url_over_mcp_url(self):
        """mcp_url should take precedence over base_url when both provided."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com",
            resource_id="sk_resource_456",
            base_url="https://preferred-base.com/",
            mcp_url="https://unused-base.com/",
        )

        assert str(provider.base_url) == "https://preferred-base.com/"

    def test_environment_variable_loading(self):
        """Test that environment variables are loaded correctly."""
        provider = ScalekitProvider(
            environment_url="https://test-env.scalekit.com",
            resource_id="sk_resource_test_456",
            base_url="http://test-server.com",
        )

        assert provider.environment_url == "https://test-env.scalekit.com"
        assert provider.resource_id == "sk_resource_test_456"
        assert str(provider.base_url) == "http://test-server.com/"

    def test_accepts_client_id_argument(self):
        """client_id parameter should be accepted but ignored."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com",
            resource_id="sk_resource_456",
            base_url="https://myserver.com/",
            client_id="client_123",
        )

        assert str(provider.base_url) == "https://myserver.com/"

    def test_url_trailing_slash_handling(self):
        """Test that URLs handle trailing slashes correctly."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com/",
            resource_id="sk_resource_456",
            base_url="https://myserver.com/",
        )

        assert provider.environment_url == "https://my-env.scalekit.com"
        assert str(provider.base_url) == "https://myserver.com/"

    def test_jwt_verifier_configured_correctly(self):
        """Test that JWT verifier is configured correctly."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com",
            resource_id="sk_resource_456",
            base_url="https://myserver.com/",
        )

        # Check that JWT verifier uses the correct endpoints
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.jwks_uri == "https://my-env.scalekit.com/keys"
        assert provider.token_verifier.issuer == "https://my-env.scalekit.com"
        assert provider.token_verifier.audience is None

    def test_required_scopes_hooks_into_verifier(self):
        """Token verifier should enforce required scopes when provided."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com",
            resource_id="sk_resource_456",
            base_url="https://myserver.com/",
            required_scopes=["read"],
        )

        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.required_scopes == ["read"]

    def test_authorization_servers_configuration(self):
        """Test that authorization servers are configured correctly."""
        provider = ScalekitProvider(
            environment_url="https://my-env.scalekit.com",
            resource_id="sk_resource_456",
            base_url="https://myserver.com/",
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
            resource_id="sk_resource_test_456",
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


class TestScalekitProviderIntegration:
    async def test_unauthorized_access(self, mcp_server_url: str):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url) as client:
                tools = await client.list_tools()  # noqa: F841

        assert isinstance(exc_info.value, httpx.HTTPStatusError)
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    async def test_metadata_route_forwards_scalekit_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mcp_server_url: str,
    ) -> None:
        """Ensure Scalekit metadata route proxies upstream JSON."""

        metadata_payload = {
            "issuer": "https://test-env.scalekit.com",
            "token_endpoint": "https://test-env.scalekit.com/token",
            "authorization_endpoint": "https://test-env.scalekit.com/authorize",
        }

        class DummyResponse:
            status_code = 200

            def __init__(self, data: dict[str, str]):
                self._data = data

            def json(self):
                return self._data

            def raise_for_status(self):
                return None

        class DummyAsyncClient:
            last_url: str | None = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url: str):
                DummyAsyncClient.last_url = url
                return DummyResponse(metadata_payload)

        real_httpx_client = httpx.AsyncClient

        monkeypatch.setattr(
            "fastmcp.server.auth.providers.scalekit.httpx.AsyncClient",
            DummyAsyncClient,
        )

        base_url = mcp_server_url.rsplit("/mcp", 1)[0]
        async with real_httpx_client() as client:
            response = await client.get(
                f"{base_url}/.well-known/oauth-authorization-server"
            )

        assert response.status_code == 200
        assert response.json() == metadata_payload
        assert (
            DummyAsyncClient.last_url
            == "https://test-env.scalekit.com/.well-known/oauth-authorization-server/resources/sk_resource_test_456"
        )
