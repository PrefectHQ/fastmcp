import re

import httpx2
import pytest
from pydantic import AnyHttpUrl

from fastmcp import FastMCP
from fastmcp.server.auth import MultiAuth, RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


class LegacyTokenVerifier(TokenVerifier):
    """Mimics custom verifiers that still call the old positional super().__init__."""

    def __init__(
        self,
        base_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
    ):
        super().__init__(base_url, required_scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        return None


class OptionalScopeVerifier(TokenVerifier):
    """Request optional scopes without requiring them for token validation."""

    def get_challenge_scopes(
        self, required_scopes: list[str] | None = None
    ) -> list[str]:
        return ["openid", "email"] if required_scopes is None else required_scopes

    async def verify_token(self, token: str) -> AccessToken | None:
        return None


@pytest.fixture
def optional_scope_remote_provider() -> RemoteAuthProvider:
    return RemoteAuthProvider(
        token_verifier=OptionalScopeVerifier(required_scopes=["openid"]),
        authorization_servers=[AnyHttpUrl("https://issuer.example.com")],
        base_url="https://api.example.com",
    )


class TestAuthProviderBase:
    """Test suite for base AuthProvider behaviors that apply to all auth providers."""

    def test_token_verifier_preserves_legacy_positional_required_scopes(self):
        """Legacy positional super().__init__(base_url, required_scopes) should keep working."""
        verifier = LegacyTokenVerifier("https://my-server.com", ["read"])

        assert verifier.base_url == AnyHttpUrl("https://my-server.com/")
        assert verifier.required_scopes == ["read"]
        assert verifier.resource_base_url is None

    @pytest.mark.parametrize("wrapper", ["direct", "remote", "multi", "multi_remote"])
    @pytest.mark.parametrize("custom_default", [False, True])
    async def test_wrappers_preserve_default_challenge_scopes(
        self, wrapper: str, custom_default: bool
    ):
        verifier = (
            OptionalScopeVerifier(required_scopes=["openid"])
            if custom_default
            else StaticTokenVerifier(tokens={}, required_scopes=["openid"])
        )
        auth = verifier
        if wrapper in {"remote", "multi_remote"}:
            auth = RemoteAuthProvider(
                token_verifier=verifier,
                authorization_servers=[AnyHttpUrl("https://issuer.example.com")],
                base_url="https://api.example.com",
            )
        if wrapper == "multi":
            auth = MultiAuth(verifiers=verifier)
        elif wrapper == "multi_remote":
            auth = MultiAuth(server=auth)

        app = FastMCP("test", auth=auth).http_app()
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://api.example.com"
        ) as client:
            response = await client.get("/mcp")

        assert response.status_code == 401
        match = re.search(r'\bscope="([^"]*)"', response.headers["www-authenticate"])
        assert match is not None
        assert set(match.group(1).split()) == (
            {"openid", "email"} if custom_default else {"openid"}
        )

    @pytest.mark.parametrize("use_server", [False, True])
    @pytest.mark.parametrize("required_scopes", [["openid"], ["admin"], []])
    async def test_multi_auth_explicit_challenge_scope_override(
        self,
        optional_scope_remote_provider: RemoteAuthProvider,
        use_server: bool,
        required_scopes: list[str],
    ):
        auth = (
            MultiAuth(
                server=optional_scope_remote_provider, required_scopes=required_scopes
            )
            if use_server
            else MultiAuth(
                verifiers=optional_scope_remote_provider.token_verifier,
                required_scopes=required_scopes,
            )
        )
        app = FastMCP("test", auth=auth).http_app()
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://api.example.com"
        ) as client:
            response = await client.get("/mcp")

        assert response.status_code == 401
        match = re.search(r'\bscope="([^"]*)"', response.headers["www-authenticate"])
        scopes = set(match.group(1).split()) if match else set()
        assert scopes == set(required_scopes)

    @pytest.mark.parametrize("challenge_scopes", [["profile"], []])
    def test_remote_auth_explicit_challenge_scopes(
        self,
        optional_scope_remote_provider: RemoteAuthProvider,
        challenge_scopes: list[str],
    ):
        auth = RemoteAuthProvider(
            token_verifier=optional_scope_remote_provider.token_verifier,
            authorization_servers=optional_scope_remote_provider.authorization_servers,
            base_url=optional_scope_remote_provider.base_url,
            challenge_scopes=challenge_scopes,
        )

        assert auth.challenge_scopes == challenge_scopes
        assert auth.get_challenge_scopes(["openid"]) == challenge_scopes
        assert auth.get_challenge_scopes(["admin"]) == ["admin"]
        assert auth.get_challenge_scopes([]) == []

    @pytest.fixture
    def basic_remote_provider(self):
        """Basic RemoteAuthProvider fixture for testing base AuthProvider behaviors."""
        # Create a static token verifier with a test token
        tokens = {
            "test_token": {
                "client_id": "test-client",
                "scopes": ["read", "write"],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)
        return RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://my-server.com",
        )

    async def test_www_authenticate_header_points_to_base_url(
        self, basic_remote_provider
    ):
        """Test that WWW-Authenticate header points to RFC 9728-compliant metadata URL.

        The WWW-Authenticate header includes the resource path per RFC 9728,
        so clients can discover where the metadata is actually registered.
        """
        mcp = FastMCP("test-server", auth=basic_remote_provider)
        # Mount MCP at a non-root path
        mcp_http_app = mcp.http_app(path="/api/v1/mcp")

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=mcp_http_app),
            base_url="https://my-server.com",
        ) as client:
            # Make unauthorized request to MCP endpoint
            response = await client.get("/api/v1/mcp")
            assert response.status_code == 401

            www_auth = response.headers.get("www-authenticate", "")
            assert "resource_metadata=" in www_auth

            # Extract the metadata URL from the header
            match = re.search(r'resource_metadata="([^"]+)"', www_auth)
            assert match is not None
            metadata_url = match.group(1)

            # The metadata URL includes the resource path per RFC 9728
            assert (
                metadata_url
                == "https://my-server.com/.well-known/oauth-protected-resource/api/v1/mcp"
            )

    async def test_automatic_resource_url_capture(self, basic_remote_provider):
        """Test that resource URL is automatically captured from MCP path.

        This test verifies PR #1682 functionality where the resource URL
        should be automatically set based on the MCP endpoint path.
        """
        mcp = FastMCP("test-server", auth=basic_remote_provider)
        # Mount MCP at a specific path
        mcp_http_app = mcp.http_app(path="/mcp")

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=mcp_http_app),
            base_url="https://my-server.com",
        ) as client:
            # The .well-known metadata is at a path-aware location per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200

            data = response.json()
            # The resource URL should be automatically set to the MCP path
            assert data.get("resource") == "https://my-server.com/mcp"

    async def test_automatic_resource_url_with_nested_path(self, basic_remote_provider):
        """Test automatic resource URL capture with deeply nested MCP path."""
        mcp = FastMCP("test-server", auth=basic_remote_provider)
        mcp_http_app = mcp.http_app(path="/api/v2/services/mcp")

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=mcp_http_app),
            base_url="https://my-server.com",
        ) as client:
            # The .well-known metadata includes the resource path per RFC 9728
            response = await client.get(
                "/.well-known/oauth-protected-resource/api/v2/services/mcp"
            )
            assert response.status_code == 200

            data = response.json()
            # Should automatically capture the nested path
            assert data.get("resource") == "https://my-server.com/api/v2/services/mcp"
