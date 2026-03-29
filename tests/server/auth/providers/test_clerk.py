"""Tests for Clerk OAuth provider."""

import re

import httpx
import pytest
from key_value.aio.stores.memory import MemoryStore
from pytest_httpx import HTTPXMock

from fastmcp.server.auth.providers.clerk import ClerkProvider, ClerkTokenVerifier

CLERK_DOMAIN = "test-instance.clerk.accounts.dev"

_USERINFO_RE = re.compile(rf"https://{re.escape(CLERK_DOMAIN)}/oauth/userinfo")
_INTROSPECTION_RE = re.compile(rf"https://{re.escape(CLERK_DOMAIN)}/oauth/token_info")


@pytest.fixture
def memory_storage() -> MemoryStore:
    """Provide a MemoryStore for tests to avoid SQLite initialization on Windows."""
    return MemoryStore()


class TestClerkProvider:
    """Test Clerk OAuth provider functionality."""

    def test_init_with_explicit_params(self, memory_storage: MemoryStore):
        """Test ClerkProvider initialization with explicit parameters."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            required_scopes=["openid", "email", "profile"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._upstream_client_id == "clerk-client-id"
        assert provider._upstream_client_secret is not None
        assert (
            provider._upstream_client_secret.get_secret_value() == "clerk-client-secret"
        )
        assert str(provider.base_url) == "https://myserver.com/"

    def test_init_defaults(self, memory_storage: MemoryStore):
        """Test that default values are applied correctly."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._redirect_path == "/auth/callback"

    def test_oauth_endpoints_configured_correctly(self, memory_storage: MemoryStore):
        """Test that OAuth endpoints are derived from the domain."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert (
            provider._upstream_authorization_endpoint
            == f"https://{CLERK_DOMAIN}/oauth/authorize"
        )
        assert (
            provider._upstream_token_endpoint == f"https://{CLERK_DOMAIN}/oauth/token"
        )
        assert provider._upstream_revocation_endpoint is None

    def test_domain_trailing_slash_stripped(self, memory_storage: MemoryStore):
        """Test that trailing slashes are stripped from the domain."""
        provider = ClerkProvider(
            domain=f"{CLERK_DOMAIN}/",
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert (
            provider._upstream_authorization_endpoint
            == f"https://{CLERK_DOMAIN}/oauth/authorize"
        )

    def test_default_scopes(self, memory_storage: MemoryStore):
        """Test that default required scopes are openid, email, profile."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider is not None

    def test_custom_scopes(self, memory_storage: MemoryStore):
        """Test that custom scopes are accepted."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            required_scopes=["openid", "email", "profile", "public_metadata"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider is not None

    def test_no_extra_authorize_params_by_default(self, memory_storage: MemoryStore):
        """Test that no extra authorize params are set by default."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._extra_authorize_params in (None, {})

    def test_extra_authorize_params_passed_through(self, memory_storage: MemoryStore):
        """Test that extra authorize params are forwarded."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            extra_authorize_params={"prompt": "login"},
            client_storage=memory_storage,
        )

        assert provider._extra_authorize_params == {"prompt": "login"}

    def test_valid_scopes_passed_through(self, memory_storage: MemoryStore):
        """Test that valid_scopes is passed to OAuthProxy."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            required_scopes=["openid"],
            valid_scopes=["openid", "email", "profile", "public_metadata"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        reg_options = provider.client_registration_options
        assert reg_options is not None
        assert reg_options.valid_scopes is not None
        assert set(reg_options.valid_scopes) == {
            "openid",
            "email",
            "profile",
            "public_metadata",
        }

    def test_issuer_url_defaults_to_base_url(self, memory_storage: MemoryStore):
        """Test that issuer_url defaults to base_url when not provided."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert str(provider.issuer_url) == "https://myserver.com/"

    def test_custom_issuer_url(self, memory_storage: MemoryStore):
        """Test that a custom issuer_url is used when provided."""
        provider = ClerkProvider(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
            base_url="https://myserver.com/mcp",
            issuer_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert str(provider.issuer_url) == "https://myserver.com/"


class TestClerkTokenVerifier:
    """Test ClerkTokenVerifier.verify_token() using userinfo + introspection."""

    async def test_valid_token_basic(self, httpx_mock: HTTPXMock):
        """A valid token returns an AccessToken with user claims from userinfo."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={
                "sub": "user_abc123",
                "email": "user@example.com",
                "email_verified": True,
                "name": "Test User",
                "picture": "https://img.clerk.com/photo.jpg",
                "given_name": "Test",
                "family_name": "User",
                "preferred_username": "testuser",
                "iss": f"https://{CLERK_DOMAIN}",
            },
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={
                "active": True,
                "scope": "openid email profile",
                "aud": "clerk-client-id",
                "exp": 9999999999,
            },
        )

        verifier = ClerkTokenVerifier(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
        )
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.client_id == "clerk-client-id"
        assert result.scopes == ["openid", "email", "profile"]
        assert result.expires_at == 9999999999
        assert result.claims["sub"] == "user_abc123"
        assert result.claims["email"] == "user@example.com"
        assert result.claims["name"] == "Test User"
        assert result.claims["picture"] == "https://img.clerk.com/photo.jpg"
        assert result.claims["given_name"] == "Test"
        assert result.claims["family_name"] == "User"
        assert result.claims["preferred_username"] == "testuser"
        assert result.claims["aud"] == "clerk-client-id"

    async def test_invalid_token_returns_none(self, httpx_mock: HTTPXMock):
        """HTTP 401 from userinfo endpoint causes verify_token to return None."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            status_code=401,
            json={"error": "invalid_token"},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("expired-token")

        assert result is None

    async def test_missing_sub_returns_none(self, httpx_mock: HTTPXMock):
        """A 200 response from userinfo without 'sub' is rejected."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"email": "user@example.com"},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("token-without-sub")

        assert result is None

    async def test_introspection_inactive_token_returns_none(
        self, httpx_mock: HTTPXMock
    ):
        """Token marked inactive by introspection is rejected."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123", "email": "user@example.com"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": False},
        )

        verifier = ClerkTokenVerifier(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
        )
        result = await verifier.verify_token("inactive-token")

        assert result is None

    async def test_introspection_failure_rejects_when_scopes_required(
        self, httpx_mock: HTTPXMock
    ):
        """When introspection fails and required_scopes are set, token is rejected."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123", "email": "user@example.com"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            status_code=500,
            json={"error": "internal_server_error"},
        )

        verifier = ClerkTokenVerifier(
            domain=CLERK_DOMAIN,
            required_scopes=["openid", "email"],
        )
        result = await verifier.verify_token("valid-token")

        assert result is None

    async def test_empty_scopes_rejects_when_required(self, httpx_mock: HTTPXMock):
        """When introspection returns no scopes and required_scopes are set, token is rejected."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": ""},
        )

        verifier = ClerkTokenVerifier(
            domain=CLERK_DOMAIN,
            required_scopes=["openid", "email", "profile"],
        )
        result = await verifier.verify_token("valid-token")

        assert result is None

    async def test_required_scopes_not_satisfied_returns_none(
        self, httpx_mock: HTTPXMock
    ):
        """Token without required scopes is rejected."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid"},
        )

        verifier = ClerkTokenVerifier(
            domain=CLERK_DOMAIN,
            required_scopes=["openid", "email", "profile"],
        )
        result = await verifier.verify_token("token-missing-scopes")

        assert result is None

    async def test_uses_bearer_header_for_userinfo(self, httpx_mock: HTTPXMock):
        """verify_token sends the token as a Bearer header to userinfo."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid"},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        await verifier.verify_token("my-access-token")

        requests = httpx_mock.get_requests()
        userinfo_req = requests[0]
        assert userinfo_req.headers["Authorization"] == "Bearer my-access-token"

    async def test_introspection_sends_client_credentials(self, httpx_mock: HTTPXMock):
        """Introspection request includes client_id and client_secret in the body."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid", "aud": "clerk-client-id"},
        )

        verifier = ClerkTokenVerifier(
            domain=CLERK_DOMAIN,
            client_id="clerk-client-id",
            client_secret="clerk-client-secret",
        )
        await verifier.verify_token("my-access-token")

        requests = httpx_mock.get_requests()
        introspect_req = requests[1]
        body = introspect_req.content.decode()
        assert "client_id=clerk-client-id" in body
        assert "client_secret=clerk-client-secret" in body
        assert "token=my-access-token" in body

    async def test_expires_at_from_introspection(self, httpx_mock: HTTPXMock):
        """expires_at is set from the 'exp' claim in the introspection response."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid", "exp": 1700000000},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.expires_at == 1700000000

    async def test_client_id_falls_back_to_sub(self, httpx_mock: HTTPXMock):
        """When introspection has no aud/client_id, client_id falls back to sub."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid"},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.client_id == "user_abc123"

    async def test_aud_from_introspection_client_id_field(self, httpx_mock: HTTPXMock):
        """When introspection returns client_id but not aud, client_id is used."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid", "client_id": "my-app-id"},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.client_id == "my-app-id"
        assert result.claims["aud"] == "my-app-id"

    async def test_no_required_scopes_accepts_any(self, httpx_mock: HTTPXMock):
        """When no required_scopes are set, any valid token is accepted."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid custom_scope"},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.scopes == ["openid", "custom_scope"]

    async def test_clerk_user_data_in_claims(self, httpx_mock: HTTPXMock):
        """The full userinfo response is stored in clerk_user_data claim."""
        user_data = {
            "sub": "user_abc123",
            "email": "user@example.com",
            "name": "Test User",
        }
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json=user_data,
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.claims["clerk_user_data"] == user_data

    async def test_network_error_returns_none(self, httpx_mock: HTTPXMock):
        """Network errors during verification return None instead of raising."""
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
            url=_USERINFO_RE,
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("valid-token")

        assert result is None

    async def test_introspection_failure_accepts_without_required_scopes(
        self, httpx_mock: HTTPXMock
    ):
        """When introspection fails but no required_scopes, token is still accepted."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123", "email": "user@example.com"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            status_code=500,
            json={"error": "internal_server_error"},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.claims["sub"] == "user_abc123"

    async def test_audience_mismatch_returns_none(self, httpx_mock: HTTPXMock):
        """Token with wrong audience is rejected when client_id is configured."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid", "aud": "wrong-client-id"},
        )

        verifier = ClerkTokenVerifier(
            domain=CLERK_DOMAIN,
            client_id="my-client-id",
            client_secret="my-client-secret",
        )
        result = await verifier.verify_token("valid-token")

        assert result is None

    async def test_audience_missing_returns_none_when_client_id_set(
        self, httpx_mock: HTTPXMock
    ):
        """Token without audience is rejected when client_id is configured."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid"},
        )

        verifier = ClerkTokenVerifier(
            domain=CLERK_DOMAIN,
            client_id="my-client-id",
            client_secret="my-client-secret",
        )
        result = await verifier.verify_token("valid-token")

        assert result is None

    async def test_audience_not_checked_without_client_id(self, httpx_mock: HTTPXMock):
        """Without client_id configured, any audience is accepted."""
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "user_abc123"},
        )
        httpx_mock.add_response(
            url=_INTROSPECTION_RE,
            json={"active": True, "scope": "openid", "aud": "some-other-id"},
        )

        verifier = ClerkTokenVerifier(domain=CLERK_DOMAIN)
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.claims["aud"] == "some-other-id"
