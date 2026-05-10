"""Tests for PropelAuthProvider."""

from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from fastmcp import Client, FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.server.auth.providers.propelauth import (
    PropelAuthProvider,
    PropelAuthTokenIntrospectionOverrides,
)
from fastmcp.utilities.tests import run_server_async


class TestPropelAuthProvider:
    """Test PropelAuth's auth provider."""

    def test_init_with_only_required_params(self):
        """Test PropelAuthProvider initialization with only required params."""
        provider = PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
        )

        # Verify the provider is configured correctly
        assert len(provider.authorization_servers) == 1
        assert (
            str(provider.authorization_servers[0])
            == "https://auth.example.com/oauth/2.1"
        )
        assert str(provider.base_url) == "https://example.com/"

        # Verify token verifier is configured correctly
        assert isinstance(provider.token_verifier, IntrospectionTokenVerifier)
        assert (
            provider.token_verifier.introspection_url
            == "https://auth.example.com/oauth/2.1/introspect"
        )
        assert provider.token_verifier.client_id == "client_id_123"
        assert provider.token_verifier.client_secret == "client_secret_123"

    def test_auth_url_trailing_slash_normalization(self):
        """Test that trailing slash on auth_url is stripped before building URLs."""
        provider = PropelAuthProvider(
            auth_url="https://auth.example.com/",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
        )

        assert isinstance(provider.token_verifier, IntrospectionTokenVerifier)
        assert len(provider.authorization_servers) == 1
        assert (
            str(provider.authorization_servers[0])
            == "https://auth.example.com/oauth/2.1"
        )
        assert (
            provider.token_verifier.introspection_url
            == "https://auth.example.com/oauth/2.1/introspect"
        )

    def test_required_scopes_passed_to_verifier(self):
        """Test that required_scopes are passed through to the token verifier."""
        provider = PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
            required_scopes=["read", "write"],
        )

        assert isinstance(provider.token_verifier, IntrospectionTokenVerifier)
        assert provider.token_verifier.required_scopes == ["read", "write"]

    def test_introspection_client_secret_as_secret_str(self):
        """Test that SecretStr client_secret is unwrapped correctly."""
        provider = PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret=SecretStr("my_secret"),
            base_url="https://example.com",
        )

        assert isinstance(provider.token_verifier, IntrospectionTokenVerifier)
        assert provider.token_verifier.client_secret == "my_secret"

    def test_authorization_servers_configuration(self):
        """Test that authorization_servers contains the correct PropelAuth URL."""
        provider = PropelAuthProvider(
            auth_url="https://auth.propelauth.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
        )

        assert len(provider.authorization_servers) == 1
        assert (
            str(provider.authorization_servers[0])
            == "https://auth.propelauth.com/oauth/2.1"
        )

    def test_token_introspection_overrides_timeout(self):
        """Test that timeout_seconds override is passed to the verifier."""
        provider = PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
            token_introspection_overrides={"timeout_seconds": 30},
        )

        assert isinstance(provider.token_verifier, IntrospectionTokenVerifier)
        assert provider.token_verifier.timeout_seconds == 30

    def test_token_introspection_overrides_cache(self):
        """Test that cache overrides are passed to the verifier."""
        provider = PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
            token_introspection_overrides={
                "cache_ttl_seconds": 300,
                "max_cache_size": 500,
            },
        )

        assert isinstance(provider.token_verifier, IntrospectionTokenVerifier)
        assert provider.token_verifier._cache._ttl == 300
        assert provider.token_verifier._cache._max_size == 500

    def test_token_introspection_overrides_http_client(self):
        """Test that http_client override is passed to the verifier."""
        client = httpx.AsyncClient()
        provider = PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
            token_introspection_overrides={"http_client": client},
        )

        assert isinstance(provider.token_verifier, IntrospectionTokenVerifier)
        assert provider.token_verifier._http_client is client

    def test_token_introspection_overrides_ignores_unknown_keys(self):
        """Test that unknown override keys are silently ignored."""
        provider = PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
            # This won't typecheck without casting, since it shouldn't be allowed
            token_introspection_overrides=cast(
                PropelAuthTokenIntrospectionOverrides, {"unknown_key": "value"}
            ),
        )

        assert isinstance(provider.token_verifier, IntrospectionTokenVerifier)
        assert provider.token_verifier.timeout_seconds == 10

    def test_token_introspection_overrides_ignores_disallowed_known_keys(self):
        """Test that known IntrospectionTokenVerifier keys not in the allow list are ignored."""
        provider = PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
            # This won't typecheck without casting, since it shouldn't be allowed
            token_introspection_overrides=cast(
                PropelAuthTokenIntrospectionOverrides, {"client_id": "sneaky_override"}
            ),
        )

        assert isinstance(provider.token_verifier, IntrospectionTokenVerifier)
        assert provider.token_verifier.client_id == "client_id_123"


class TestPropelAuthResourceChecking:
    """Test audience (aud) checking when resource is configured."""

    def _make_provider(self, resource: str | None = None) -> PropelAuthProvider:
        return PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="https://example.com",
            resource=resource,
        )

    def _make_access_token(self, aud: str) -> AccessToken:
        return AccessToken(
            token="test-token",
            client_id="client_id_123",
            scopes=[],
            claims={"active": True, "sub": "user-1", "aud": aud},
        )

    async def test_no_resource_skips_aud_check(self, monkeypatch: pytest.MonkeyPatch):
        """When resource is not configured, tokens are accepted without aud checking."""
        provider = self._make_provider(resource=None)
        token = self._make_access_token(aud="https://anything.example.com")
        monkeypatch.setattr(
            provider.token_verifier, "verify_token", AsyncMock(return_value=token)
        )

        result = await provider.verify_token("test-token")
        assert result is token

    async def test_aud_matches_resource(self, monkeypatch: pytest.MonkeyPatch):
        """Token is accepted when aud matches the configured resource."""
        provider = self._make_provider(resource="https://api.example.com/mcp")
        token = self._make_access_token(aud="https://api.example.com/mcp")
        monkeypatch.setattr(
            provider.token_verifier, "verify_token", AsyncMock(return_value=token)
        )

        result = await provider.verify_token("test-token")
        assert result is token

    async def test_aud_does_not_match_resource(self, monkeypatch: pytest.MonkeyPatch):
        """Token is rejected when aud doesn't match the configured resource."""
        provider = self._make_provider(resource="https://api.example.com/mcp")
        token = self._make_access_token(aud="https://other-server.example.com/mcp")
        monkeypatch.setattr(
            provider.token_verifier, "verify_token", AsyncMock(return_value=token)
        )

        result = await provider.verify_token("test-token")
        assert result is None

    async def test_inner_verifier_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        """When the inner verifier rejects the token, None is returned without aud checking."""
        provider = self._make_provider(resource="https://api.example.com/mcp")
        monkeypatch.setattr(
            provider.token_verifier, "verify_token", AsyncMock(return_value=None)
        )

        result = await provider.verify_token("test-token")
        assert result is None


@pytest.fixture
async def mcp_server_url():
    """Start MCP server with PropelAuth authentication."""
    mcp = FastMCP(
        auth=PropelAuthProvider(
            auth_url="https://auth.example.com",
            introspection_client_id="client_id_123",
            introspection_client_secret="client_secret_123",
            base_url="http://localhost:4321",
        )
    )

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    async with run_server_async(mcp, transport="http") as url:
        yield url


class TestPropelAuthProviderIntegration:
    async def test_unauthorized_access(self, mcp_server_url: str):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url) as client:
                tools = await client.list_tools()  # noqa: F841

        assert isinstance(exc_info.value, httpx.HTTPStatusError)
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    async def test_metadata_route_forwards_propelauth_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mcp_server_url: str,
    ) -> None:
        """Ensure PropelAuth metadata route proxies upstream JSON."""

        metadata_payload = {
            "issuer": "https://auth.example.com",
            "token_endpoint": "https://auth.example.com/oauth/2.1/token",
            "authorization_endpoint": "https://auth.example.com/oauth/2.1/authorize",
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
            "fastmcp.server.auth.providers.propelauth.httpx.AsyncClient",
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
            == "https://auth.example.com/.well-known/oauth-authorization-server/oauth/2.1"
        )
