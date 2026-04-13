"""Tests for WorkOS OAuth provider."""

from urllib.parse import urlparse

import httpx
import pytest
from key_value.aio.stores.memory import MemoryStore
from pytest_httpx import HTTPXMock

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.workos import (
    AuthKitProvider,
    WorkOSProvider,
    WorkOSTokenVerifier,
)
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async


@pytest.fixture
def memory_storage() -> MemoryStore:
    """Provide a MemoryStore for tests to avoid SQLite initialization on Windows."""
    return MemoryStore()


class TestWorkOSProvider:
    """Test WorkOS OAuth provider functionality."""

    def test_init_with_explicit_params(self, memory_storage: MemoryStore):
        """Test WorkOSProvider initialization with explicit parameters."""
        provider = WorkOSProvider(
            client_id="client_test123",
            client_secret="secret_test456",
            authkit_domain="https://test.authkit.app",
            base_url="https://myserver.com",
            required_scopes=["openid", "profile"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._upstream_client_id == "client_test123"
        assert provider._upstream_client_secret is not None
        assert provider._upstream_client_secret.get_secret_value() == "secret_test456"
        assert str(provider.base_url) == "https://myserver.com/"

    def test_authkit_domain_https_prefix_handling(self, memory_storage: MemoryStore):
        """Test that authkit_domain handles missing https:// prefix."""
        # Without https:// - should add it
        provider1 = WorkOSProvider(
            client_id="test_client",
            client_secret="test_secret",
            authkit_domain="test.authkit.app",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
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
            client_storage=memory_storage,
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
            client_storage=memory_storage,
        )
        parsed = urlparse(provider3._upstream_authorization_endpoint)
        assert parsed.scheme == "http"
        assert parsed.netloc == "localhost:8080"
        assert parsed.path == "/oauth2/authorize"

    def test_init_defaults(self, memory_storage: MemoryStore):
        """Test that default values are applied correctly."""
        provider = WorkOSProvider(
            client_id="test_client",
            client_secret="test_secret",
            authkit_domain="https://test.authkit.app",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Check defaults
        assert provider._redirect_path == "/auth/callback"
        # WorkOS provider has no default scopes but we can't easily verify without accessing internals

    def test_oauth_endpoints_configured_correctly(self, memory_storage: MemoryStore):
        """Test that OAuth endpoints are configured correctly."""
        provider = WorkOSProvider(
            client_id="test_client",
            client_secret="test_secret",
            authkit_domain="https://test.authkit.app",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
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
    async def test_unauthorized_access(
        self, memory_storage: MemoryStore, mcp_server_url: str
    ):
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


class TestAuthKitAudienceBinding:
    """RFC 8707 resource-indicator audience binding.

    AuthKit mints tokens with ``aud`` equal to the resource URL the client
    requested — which must equal the URL FastMCP advertises in its protected
    resource metadata. AuthKitProvider auto-wires that equality: once the
    MCP mount path is known, ``JWTVerifier.audience`` is set to
    ``_get_resource_url(mcp_path)``.
    """

    def test_audience_binds_to_resource_url_on_set_mcp_path(self):
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        provider = AuthKitProvider(
            authkit_domain="https://test.authkit.app",
            base_url="http://127.0.0.1:8000",
        )

        verifier = provider.token_verifier
        assert isinstance(verifier, JWTVerifier)
        # Audience unset before the path is known — provider has no way to
        # compute the resource URL yet.
        assert verifier.audience is None

        provider.set_mcp_path("/mcp")

        expected = str(provider._get_resource_url("/mcp"))
        assert verifier.audience == expected
        assert expected == "http://127.0.0.1:8000/mcp"

    def test_audience_respects_resource_base_url(self):
        """When ``resource_base_url`` differs from ``base_url``, the audience
        follows the advertised resource URL, not the OAuth-surface URL."""
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        provider = AuthKitProvider(
            authkit_domain="https://test.authkit.app",
            base_url="https://oauth.example.com",
            resource_base_url="https://api.example.com",
        )
        provider.set_mcp_path("/mcp")

        verifier = provider.token_verifier
        assert isinstance(verifier, JWTVerifier)
        assert verifier.audience == "https://api.example.com/mcp"

    def test_custom_token_verifier_audience_not_overwritten(self):
        """If the caller supplies their own verifier, we treat its audience
        as intentional and do not touch it."""
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        custom_audience = "https://some-other-resource.example.com"
        custom = JWTVerifier(
            jwks_uri="https://test.authkit.app/oauth2/jwks",
            issuer="https://test.authkit.app",
            audience=custom_audience,
        )
        provider = AuthKitProvider(
            authkit_domain="https://test.authkit.app",
            base_url="http://127.0.0.1:8000",
            token_verifier=custom,
        )
        provider.set_mcp_path("/mcp")

        assert provider.token_verifier is custom
        assert custom.audience == custom_audience

    def test_audience_binds_through_http_app(self):
        """End-to-end: mounting a FastMCP server triggers the lifecycle hook
        that populates ``JWTVerifier.audience``."""
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        auth = AuthKitProvider(
            authkit_domain="https://test.authkit.app",
            base_url="http://127.0.0.1:8000",
        )
        mcp = FastMCP("test", auth=auth)
        mcp.http_app(path="/mcp")

        verifier = auth.token_verifier
        assert isinstance(verifier, JWTVerifier)
        assert verifier.audience == "http://127.0.0.1:8000/mcp"


class TestWorkOSTokenVerifierScopes:
    async def test_verify_token_rejects_missing_required_scopes(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            url="https://test.authkit.app/oauth2/userinfo",
            status_code=200,
            json={
                "sub": "user_123",
                "email": "user@example.com",
                "scope": "openid profile",
            },
        )

        verifier = WorkOSTokenVerifier(
            authkit_domain="https://test.authkit.app",
            required_scopes=["read:secrets"],
        )

        result = await verifier.verify_token("token")

        assert result is None

    async def test_verify_token_returns_actual_token_scopes(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            url="https://test.authkit.app/oauth2/userinfo",
            status_code=200,
            json={
                "sub": "user_123",
                "email": "user@example.com",
                "scope": "openid profile read:secrets",
            },
        )

        verifier = WorkOSTokenVerifier(
            authkit_domain="https://test.authkit.app",
            required_scopes=["read:secrets"],
        )

        result = await verifier.verify_token("token")

        assert result is not None
        assert result.scopes == ["openid", "profile", "read:secrets"]
