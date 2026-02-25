"""Tests for http_client parameter on token verifiers.

Verifies that all token verifiers accept an optional httpx.AsyncClient for
connection pooling (issues #3287 and #3293).
"""

import time

import httpx
import pytest
from pytest_httpx import HTTPXMock

from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair


class TestIntrospectionHttpClient:
    """Test http_client parameter on IntrospectionTokenVerifier."""

    @pytest.fixture
    def shared_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=30)

    def test_stores_http_client(self, shared_client: httpx.AsyncClient):
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/introspect",
            client_id="test",
            client_secret="secret",
            http_client=shared_client,
        )
        assert verifier._http_client is shared_client

    def test_default_http_client_is_none(self):
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/introspect",
            client_id="test",
            client_secret="secret",
        )
        assert verifier._http_client is None

    async def test_uses_provided_client(
        self, shared_client: httpx.AsyncClient, httpx_mock: HTTPXMock
    ):
        """When http_client is provided, it should be used for requests."""
        httpx_mock.add_response(
            url="https://auth.example.com/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-1",
                "scope": "read",
                "exp": int(time.time()) + 3600,
            },
        )

        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/introspect",
            client_id="test",
            client_secret="secret",
            http_client=shared_client,
        )

        result = await verifier.verify_token("tok")
        assert result is not None
        assert result.client_id == "user-1"

    async def test_client_not_closed_after_call(
        self, shared_client: httpx.AsyncClient, httpx_mock: HTTPXMock
    ):
        """User-provided client must not be closed by the verifier."""
        httpx_mock.add_response(
            url="https://auth.example.com/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-1",
                "scope": "read",
                "exp": int(time.time()) + 3600,
            },
        )

        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/introspect",
            client_id="test",
            client_secret="secret",
            http_client=shared_client,
        )

        await verifier.verify_token("tok")
        # Client should still be open — not closed by the verifier
        assert not shared_client.is_closed

    async def test_reuses_client_across_calls(
        self, shared_client: httpx.AsyncClient, httpx_mock: HTTPXMock
    ):
        """Same client instance should be reused across multiple verify_token calls."""
        for _ in range(3):
            httpx_mock.add_response(
                url="https://auth.example.com/introspect",
                method="POST",
                json={
                    "active": True,
                    "client_id": "user-1",
                    "scope": "read",
                    "exp": int(time.time()) + 3600,
                },
            )

        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/introspect",
            client_id="test",
            client_secret="secret",
            http_client=shared_client,
        )

        for _ in range(3):
            result = await verifier.verify_token("tok")
            assert result is not None

        assert not shared_client.is_closed


class TestJWTVerifierHttpClient:
    """Test http_client parameter on JWTVerifier."""

    @pytest.fixture(scope="class")
    def rsa_key_pair(self) -> RSAKeyPair:
        return RSAKeyPair.generate()

    @pytest.fixture
    def shared_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=30)

    def test_stores_http_client(self, shared_client: httpx.AsyncClient):
        verifier = JWTVerifier(
            jwks_uri="https://auth.example.com/.well-known/jwks.json",
            http_client=shared_client,
        )
        assert verifier._http_client is shared_client

    def test_default_http_client_is_none(self):
        verifier = JWTVerifier(
            jwks_uri="https://auth.example.com/.well-known/jwks.json",
        )
        assert verifier._http_client is None

    async def test_jwks_fetch_uses_provided_client(
        self,
        rsa_key_pair: RSAKeyPair,
        shared_client: httpx.AsyncClient,
        httpx_mock: HTTPXMock,
    ):
        """When http_client is provided, JWKS fetches should use it."""
        from authlib.jose import JsonWebKey

        # Build a JWKS response from the RSA key pair
        public_key_obj = JsonWebKey.import_key(rsa_key_pair.public_key)
        jwk_dict = dict(public_key_obj.as_dict())
        jwk_dict["kid"] = "test-key-1"
        jwk_dict["use"] = "sig"
        jwk_dict["alg"] = "RS256"

        httpx_mock.add_response(
            url="https://auth.example.com/.well-known/jwks.json",
            json={"keys": [jwk_dict]},
        )

        verifier = JWTVerifier(
            jwks_uri="https://auth.example.com/.well-known/jwks.json",
            issuer="https://auth.example.com",
            http_client=shared_client,
        )

        token = rsa_key_pair.create_token(
            issuer="https://auth.example.com",
            kid="test-key-1",
        )

        result = await verifier.verify_token(token)
        assert result is not None
        assert not shared_client.is_closed

    async def test_ssrf_safe_ignores_http_client(
        self,
        shared_client: httpx.AsyncClient,
    ):
        """When ssrf_safe=True, the custom http_client should NOT be used."""
        verifier = JWTVerifier(
            jwks_uri="https://auth.example.com/.well-known/jwks.json",
            ssrf_safe=True,
            http_client=shared_client,
        )

        # ssrf_safe uses ssrf_safe_fetch instead of httpx.AsyncClient
        # The http_client is stored but not used in this code path
        assert verifier._http_client is shared_client
        assert verifier.ssrf_safe is True


class TestGitHubHttpClient:
    """Test http_client parameter on GitHubTokenVerifier."""

    def test_stores_http_client(self):
        from fastmcp.server.auth.providers.github import GitHubTokenVerifier

        client = httpx.AsyncClient()
        verifier = GitHubTokenVerifier(http_client=client)
        assert verifier._http_client is client

    async def test_uses_provided_client(self, httpx_mock: HTTPXMock):
        from fastmcp.server.auth.providers.github import GitHubTokenVerifier

        client = httpx.AsyncClient()
        httpx_mock.add_response(
            url="https://api.github.com/user",
            json={"id": 123, "login": "testuser"},
        )
        httpx_mock.add_response(
            url="https://api.github.com/user/repos",
            headers={"x-oauth-scopes": "user,repo"},
            json=[],
        )

        verifier = GitHubTokenVerifier(http_client=client)
        result = await verifier.verify_token("ghp_test")
        assert result is not None
        assert not client.is_closed


class TestDiscordHttpClient:
    """Test http_client parameter on DiscordTokenVerifier."""

    def test_stores_http_client(self):
        from fastmcp.server.auth.providers.discord import DiscordTokenVerifier

        client = httpx.AsyncClient()
        verifier = DiscordTokenVerifier(http_client=client)
        assert verifier._http_client is client


class TestGoogleHttpClient:
    """Test http_client parameter on GoogleTokenVerifier."""

    def test_stores_http_client(self):
        from fastmcp.server.auth.providers.google import GoogleTokenVerifier

        client = httpx.AsyncClient()
        verifier = GoogleTokenVerifier(http_client=client)
        assert verifier._http_client is client


class TestWorkOSHttpClient:
    """Test http_client parameter on WorkOSTokenVerifier."""

    def test_stores_http_client(self):
        from fastmcp.server.auth.providers.workos import WorkOSTokenVerifier

        client = httpx.AsyncClient()
        verifier = WorkOSTokenVerifier(
            authkit_domain="https://test.authkit.app",
            http_client=client,
        )
        assert verifier._http_client is client


class TestProviderHttpClientPassthrough:
    """Test that convenience providers pass http_client to their verifiers."""

    def test_github_provider_threads_http_client(self):
        from fastmcp.server.auth.providers.github import (
            GitHubProvider,
            GitHubTokenVerifier,
        )

        client = httpx.AsyncClient()
        provider = GitHubProvider(
            client_id="test",
            client_secret="secret",
            base_url="https://example.com",
            http_client=client,
        )
        # OAuthProxy stores token verifier as _token_validator
        verifier = provider._token_validator
        assert isinstance(verifier, GitHubTokenVerifier)
        assert verifier._http_client is client

    def test_discord_provider_threads_http_client(self):
        from fastmcp.server.auth.providers.discord import (
            DiscordProvider,
            DiscordTokenVerifier,
        )

        client = httpx.AsyncClient()
        provider = DiscordProvider(
            client_id="test",
            client_secret="secret",
            base_url="https://example.com",
            http_client=client,
        )
        verifier = provider._token_validator
        assert isinstance(verifier, DiscordTokenVerifier)
        assert verifier._http_client is client

    def test_google_provider_threads_http_client(self):
        from fastmcp.server.auth.providers.google import (
            GoogleProvider,
            GoogleTokenVerifier,
        )

        client = httpx.AsyncClient()
        provider = GoogleProvider(
            client_id="test",
            client_secret="secret",
            base_url="https://example.com",
            http_client=client,
        )
        verifier = provider._token_validator
        assert isinstance(verifier, GoogleTokenVerifier)
        assert verifier._http_client is client

    def test_workos_provider_threads_http_client(self):
        from fastmcp.server.auth.providers.workos import (
            WorkOSProvider,
            WorkOSTokenVerifier,
        )

        client = httpx.AsyncClient()
        provider = WorkOSProvider(
            client_id="test",
            client_secret="secret",
            authkit_domain="https://test.authkit.app",
            base_url="https://example.com",
            http_client=client,
        )
        verifier = provider._token_validator
        assert isinstance(verifier, WorkOSTokenVerifier)
        assert verifier._http_client is client

    def test_azure_provider_threads_http_client(self):
        from fastmcp.server.auth.providers.azure import AzureProvider
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        client = httpx.AsyncClient()
        provider = AzureProvider(
            client_id="test-client-id",
            client_secret="secret",
            tenant_id="test-tenant-id",
            required_scopes=["read"],
            base_url="https://example.com",
            http_client=client,
        )
        verifier = provider._token_validator
        assert isinstance(verifier, JWTVerifier)
        assert verifier._http_client is client
