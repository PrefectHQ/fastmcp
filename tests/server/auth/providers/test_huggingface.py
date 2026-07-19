"""Tests for Hugging Face OAuth provider."""

import re

import pytest
from key_value.aio.stores.memory import MemoryStore

from fastmcp.server.auth.providers.huggingface import (
    DEFAULT_HUGGINGFACE_SCOPES,
    HUGGINGFACE_AUTHORIZATION_ENDPOINT,
    HUGGINGFACE_TOKEN_ENDPOINT,
    HUGGINGFACE_USERINFO_ENDPOINT,
    HUGGINGFACE_WHOAMI_ENDPOINT,
    HuggingFaceProvider,
    HuggingFaceTokenVerifier,
)
from tests.utilities.httpx2_mock import HTTPXMock


@pytest.fixture
def memory_storage() -> MemoryStore:
    """Provide a MemoryStore for tests to avoid SQLite initialization on Windows."""
    return MemoryStore()


_USERINFO_RE = re.compile(re.escape(HUGGINGFACE_USERINFO_ENDPOINT))
_WHOAMI_RE = re.compile(re.escape(HUGGINGFACE_WHOAMI_ENDPOINT))


class TestHuggingFaceProvider:
    """Test HuggingFaceProvider functionality."""

    def test_init_with_explicit_params(self, memory_storage: MemoryStore):
        provider = HuggingFaceProvider(
            client_id="hf-client-id",
            client_secret="hf-client-secret",
            base_url="https://myserver.com",
            required_scopes=["openid", "profile", "inference-api"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._upstream_client_id == "hf-client-id"
        assert provider._upstream_client_secret is not None
        assert provider._upstream_client_secret.get_secret_value() == "hf-client-secret"
        assert str(provider.base_url) == "https://myserver.com/"

    def test_init_defaults(self, memory_storage: MemoryStore):
        provider = HuggingFaceProvider(
            client_id="hf-client-id",
            client_secret="hf-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._redirect_path == "/auth/callback"
        assert provider.required_scopes == DEFAULT_HUGGINGFACE_SCOPES
        assert provider._token_validator.required_scopes == []

    def test_oauth_endpoints_configured_correctly(self, memory_storage: MemoryStore):
        provider = HuggingFaceProvider(
            client_id="hf-client-id",
            client_secret="hf-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert (
            provider._upstream_authorization_endpoint
            == HUGGINGFACE_AUTHORIZATION_ENDPOINT
        )
        assert provider._upstream_token_endpoint == HUGGINGFACE_TOKEN_ENDPOINT
        assert provider._upstream_revocation_endpoint is None

    def test_public_pkce_app_uses_none_token_auth(self, memory_storage: MemoryStore):
        provider = HuggingFaceProvider(
            client_id="https://client.example.com/.well-known/oauth-cimd",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._upstream_client_secret is None
        assert provider._token_endpoint_auth_method == "none"

    def test_uses_upstream_token_response_scopes(self, memory_storage: MemoryStore):
        provider = HuggingFaceProvider(
            client_id="hf-client-id",
            client_secret="hf-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._uses_alternate_verification() is True

    def test_valid_scopes_passed_through(self, memory_storage: MemoryStore):
        provider = HuggingFaceProvider(
            client_id="hf-client-id",
            client_secret="hf-client-secret",
            base_url="https://myserver.com",
            required_scopes=["openid", "profile"],
            valid_scopes=["openid", "profile", "inference-api", "jobs"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        reg_options = provider.client_registration_options
        assert reg_options is not None
        assert reg_options.valid_scopes == [
            "openid",
            "profile",
            "inference-api",
            "jobs",
        ]


class TestHuggingFaceTokenVerifier:
    """Test HuggingFaceTokenVerifier.verify_token()."""

    async def test_valid_token_with_userinfo_scopes(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={
                "sub": "user-123",
                "preferred_username": "alice",
                "name": "Alice",
                "email": "alice@example.com",
                "email_verified": True,
                "picture": "https://huggingface.co/alice.png",
                "scope": "openid profile email",
            },
        )

        verifier = HuggingFaceTokenVerifier(required_scopes=["openid", "email"])
        result = await verifier.verify_token("hf_oauth_token")

        assert result is not None
        assert result.client_id == "user-123"
        assert result.scopes == ["openid", "profile", "email"]
        assert result.claims["sub"] == "user-123"
        assert result.claims["preferred_username"] == "alice"
        assert result.claims["email"] == "alice@example.com"

    async def test_valid_token_with_whoami_scopes(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={
                "sub": "user-123",
                "preferred_username": "alice",
                "scope": "openid profile",
            },
        )
        httpx_mock.add_response(
            url=_WHOAMI_RE,
            json={
                "name": "alice",
                "auth": {
                    "accessToken": {"scopes": ["openid", "profile", "inference-api"]}
                },
            },
        )

        verifier = HuggingFaceTokenVerifier(required_scopes=["openid", "inference-api"])
        result = await verifier.verify_token("hf_oauth_token")

        assert result is not None
        assert result.scopes == ["openid", "profile", "inference-api"]
        assert result.claims["huggingface_whoami"] is not None

    async def test_defaults_scopes_when_userinfo_has_no_scope(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user-123", "preferred_username": "alice"},
        )
        httpx_mock.add_response(url=_WHOAMI_RE, status_code=404)

        verifier = HuggingFaceTokenVerifier()
        result = await verifier.verify_token("hf_oauth_token")

        assert result is not None
        assert result.scopes == DEFAULT_HUGGINGFACE_SCOPES

    async def test_missing_required_scope_returns_none(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={
                "sub": "user-123",
                "scope": "openid profile",
            },
        )
        httpx_mock.add_response(url=_WHOAMI_RE, json={"name": "alice"})

        verifier = HuggingFaceTokenVerifier(required_scopes=["inference-api"])
        result = await verifier.verify_token("hf_oauth_token")

        assert result is None

    async def test_invalid_token_returns_none(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_USERINFO_RE,
            status_code=401,
            json={"error": "invalid_token"},
        )

        verifier = HuggingFaceTokenVerifier()
        result = await verifier.verify_token("invalid")

        assert result is None

    async def test_missing_sub_returns_none(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"preferred_username": "alice"},
        )

        verifier = HuggingFaceTokenVerifier()
        result = await verifier.verify_token("token-without-sub")

        assert result is None

    async def test_sends_bearer_token_to_userinfo(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user-123", "scope": "openid profile"},
        )

        verifier = HuggingFaceTokenVerifier()
        await verifier.verify_token("hf_oauth_token")

        request = httpx_mock.get_requests()[0]
        assert request.headers["Authorization"] == "Bearer hf_oauth_token"
