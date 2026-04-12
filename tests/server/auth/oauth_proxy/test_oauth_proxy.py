"""Tests for OAuth proxy initialization and configuration."""

import httpx
import pytest
from authlib.integrations.httpx_client import AsyncOAuth2Client
from key_value.aio.stores.memory import MemoryStore
from starlette.applications import Starlette

from fastmcp.server.auth.oauth_proxy import OAuthProxy


class TestOAuthProxyInitialization:
    """Tests for OAuth proxy initialization and configuration."""

    def test_basic_initialization(self, jwt_verifier):
        """Test basic proxy initialization with required parameters."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        assert (
            proxy._upstream_authorization_endpoint
            == "https://auth.example.com/authorize"
        )
        assert proxy._upstream_token_endpoint == "https://auth.example.com/token"
        assert proxy._upstream_client_id == "client-123"
        assert proxy._upstream_client_secret is not None
        assert proxy._upstream_client_secret.get_secret_value() == "secret-456"
        assert str(proxy.base_url) == "https://api.example.com/"

    def test_all_optional_parameters(self, jwt_verifier):
        """Test initialization with all optional parameters."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            upstream_revocation_endpoint="https://auth.example.com/revoke",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            redirect_path="/custom/callback",
            issuer_url="https://issuer.example.com",
            service_documentation_url="https://docs.example.com",
            allowed_client_redirect_uris=["http://localhost:*"],
            valid_scopes=["custom", "scopes"],
            forward_pkce=False,
            token_endpoint_auth_method="client_secret_post",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        assert proxy._upstream_revocation_endpoint == "https://auth.example.com/revoke"
        assert proxy._redirect_path == "/custom/callback"
        assert proxy._forward_pkce is False
        assert proxy._token_endpoint_auth_method == "client_secret_post"
        assert proxy.client_registration_options is not None
        assert proxy.client_registration_options.valid_scopes == ["custom", "scopes"]

    def test_default_scope_str_prefers_valid_scopes(self, jwt_verifier):
        """When valid_scopes is provided, _default_scope_str should use it
        instead of required_scopes. This ensures CIMD clients (which bypass
        RegistrationHandler) get registered with the full set of valid scopes."""
        jwt_verifier.required_scopes = ["openid"]
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            valid_scopes=["openid", "email", "calendar"],
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        assert proxy._default_scope_str == "openid email calendar"

    def test_default_scope_str_falls_back_to_required_scopes(self, jwt_verifier):
        """Without valid_scopes, _default_scope_str falls back to required_scopes."""
        jwt_verifier.required_scopes = ["openid"]
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        assert proxy._default_scope_str == "openid"

    def test_redirect_path_normalization(self, jwt_verifier):
        """Test that redirect_path is normalized with leading slash."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.com/authorize",
            upstream_token_endpoint="https://auth.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://api.com",
            redirect_path="auth/callback",  # No leading slash
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        assert proxy._redirect_path == "/auth/callback"

    async def test_metadata_advertises_cimd_support(self, jwt_verifier):
        """OAuth metadata should advertise CIMD support when enabled."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            enable_cimd=True,
        )

        app = Starlette(routes=proxy.get_routes())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport, base_url="https://api.example.com"
        ) as client:
            response = await client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        metadata = response.json()
        assert metadata.get("client_id_metadata_document_supported") is True


class TestOptionalClientSecret:
    """Tests for OAuthProxy without upstream_client_secret."""

    def test_no_secret_requires_jwt_signing_key(self, jwt_verifier):
        """OAuthProxy requires jwt_signing_key when client_secret is omitted."""
        with pytest.raises(ValueError, match="jwt_signing_key is required"):
            OAuthProxy(
                upstream_authorization_endpoint="https://auth.example.com/authorize",
                upstream_token_endpoint="https://auth.example.com/token",
                upstream_client_id="client-123",
                token_verifier=jwt_verifier,
                base_url="https://api.example.com",
                client_storage=MemoryStore(),
            )

    def test_no_secret_with_jwt_key_succeeds(self, jwt_verifier):
        """OAuthProxy initializes successfully without client_secret when jwt_signing_key is given."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key=b"a" * 32,
            client_storage=MemoryStore(),
        )
        assert proxy._upstream_client_secret is None
        assert proxy._upstream_client_id == "client-123"

    def test_factory_method_without_secret(self, jwt_verifier):
        """_create_upstream_oauth_client works when no secret is configured."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key=b"a" * 32,
            client_storage=MemoryStore(),
        )
        client = proxy._create_upstream_oauth_client()
        assert isinstance(client, AsyncOAuth2Client)
        assert client.client_id == "client-123"

    def test_factory_method_with_secret(self, jwt_verifier):
        """_create_upstream_oauth_client includes the secret when configured."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        client = proxy._create_upstream_oauth_client()
        assert isinstance(client, AsyncOAuth2Client)
        assert client.client_secret == "secret-456"

    def test_consent_cookies_work_without_secret(self, jwt_verifier):
        """Cookie signing/verification works using JWT key when no secret is configured."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key=b"a" * 32,
            client_storage=MemoryStore(),
        )
        signed = proxy._sign_cookie("test-payload")
        assert proxy._verify_cookie(signed) == "test-payload"
        assert proxy._verify_cookie("tampered.payload") is None
