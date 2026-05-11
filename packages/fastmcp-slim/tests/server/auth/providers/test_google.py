"""Tests for Google OAuth provider."""

import re
import time

import pytest
from key_value.aio.stores.memory import MemoryStore
from pytest_httpx import HTTPXMock

from fastmcp.server.auth.providers.google import (
    GOOGLE_SCOPE_ALIASES,
    GoogleProvider,
    GoogleTokenVerifier,
    _normalize_google_scope,
)


@pytest.fixture
def memory_storage() -> MemoryStore:
    """Provide a MemoryStore for tests to avoid SQLite initialization on Windows."""
    return MemoryStore()


class TestGoogleProvider:
    """Test Google OAuth provider functionality."""

    def test_init_with_explicit_params(self, memory_storage: MemoryStore):
        """Test GoogleProvider initialization with explicit parameters."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            required_scopes=["openid", "email", "profile"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._upstream_client_id == "123456789.apps.googleusercontent.com"
        assert provider._upstream_client_secret is not None
        assert provider._upstream_client_secret.get_secret_value() == "GOCSPX-test123"
        assert str(provider.base_url) == "https://myserver.com/"

    def test_init_defaults(self, memory_storage: MemoryStore):
        """Test that default values are applied correctly."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Check defaults
        assert provider._redirect_path == "/auth/callback"
        # Google provider has ["openid"] as default but we can't easily verify without accessing internals

    def test_oauth_endpoints_configured_correctly(self, memory_storage: MemoryStore):
        """Test that OAuth endpoints are configured correctly."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Check that endpoints use Google's OAuth2 endpoints
        assert (
            provider._upstream_authorization_endpoint
            == "https://accounts.google.com/o/oauth2/v2/auth"
        )
        assert (
            provider._upstream_token_endpoint == "https://oauth2.googleapis.com/token"
        )
        # Google provider doesn't currently set a revocation endpoint
        assert provider._upstream_revocation_endpoint is None

    def test_google_specific_scopes(self, memory_storage: MemoryStore):
        """Test handling of Google-specific scope formats."""
        # Just test that the provider accepts Google-specific scopes without error
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            required_scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Provider should initialize successfully with these scopes
        assert provider is not None

    def test_extra_authorize_params_defaults(self, memory_storage: MemoryStore):
        """Test that Google-specific defaults are set for refresh token support."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Should have Google-specific defaults for refresh token support
        assert provider._extra_authorize_params == {
            "access_type": "offline",
            "prompt": "consent",
        }

    def test_extra_authorize_params_override_defaults(
        self, memory_storage: MemoryStore
    ):
        """Test that user can override default extra authorize params."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            extra_authorize_params={"prompt": "select_account"},
            client_storage=memory_storage,
        )

        # User override should replace the default
        assert provider._extra_authorize_params["prompt"] == "select_account"
        # But other defaults should remain
        assert provider._extra_authorize_params["access_type"] == "offline"

    def test_extra_authorize_params_add_new_params(self, memory_storage: MemoryStore):
        """Test that user can add additional authorize params."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            extra_authorize_params={"login_hint": "user@example.com"},
            client_storage=memory_storage,
        )

        # New param should be added
        assert provider._extra_authorize_params["login_hint"] == "user@example.com"
        # Defaults should still be present
        assert provider._extra_authorize_params["access_type"] == "offline"
        assert provider._extra_authorize_params["prompt"] == "consent"

    def test_valid_scopes_passed_through(self, memory_storage: MemoryStore):
        """Test that valid_scopes is passed to OAuthProxy."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            required_scopes=["openid"],
            valid_scopes=["openid", "email", "profile"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        reg_options = provider.client_registration_options
        assert reg_options is not None
        assert reg_options.valid_scopes is not None
        # Shorthands should be normalized to full URIs
        assert set(reg_options.valid_scopes) == {
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        }

    def test_valid_scopes_defaults_to_required(self, memory_storage: MemoryStore):
        """Test that valid_scopes defaults to required_scopes when not provided."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            required_scopes=["openid", "email"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        reg_options = provider.client_registration_options
        assert reg_options is not None
        assert reg_options.valid_scopes is not None
        # Should fall back to the (normalized) required_scopes
        assert set(reg_options.valid_scopes) == {
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
        }


class TestGoogleScopeNormalization:
    """Test Google scope shorthand normalization."""

    @pytest.mark.parametrize(
        "shorthand, expected",
        [
            ("email", "https://www.googleapis.com/auth/userinfo.email"),
            ("profile", "https://www.googleapis.com/auth/userinfo.profile"),
            ("openid", "openid"),
            (
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.email",
            ),
            (
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/calendar",
            ),
        ],
    )
    def test_normalize_google_scope(self, shorthand: str, expected: str):
        assert _normalize_google_scope(shorthand) == expected

    def test_verifier_normalizes_required_scopes(self):
        """GoogleTokenVerifier should normalize shorthands in required_scopes."""
        verifier = GoogleTokenVerifier(
            required_scopes=["openid", "email", "profile"],
        )

        assert set(verifier.required_scopes) == {
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        }

    def test_verifier_full_uris_unchanged(self):
        """Full URIs should pass through normalization unchanged."""
        scopes = [
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
        ]
        verifier = GoogleTokenVerifier(required_scopes=scopes)
        assert verifier.required_scopes == scopes

    def test_alias_map_is_bidirectional(self):
        """Verify the alias map covers the known Google shorthands."""
        assert "email" in GOOGLE_SCOPE_ALIASES
        assert "profile" in GOOGLE_SCOPE_ALIASES


# Regex patterns for URL matching (tokeninfo uses query params)
_TOKENINFO_RE = re.compile(r"https://oauth2\.googleapis\.com/tokeninfo")
_USERINFO_RE = re.compile(r"https://www\.googleapis\.com/oauth2/v2/userinfo")


class TestGoogleTokenVerifier:
    """Test GoogleTokenVerifier.verify_token() using the tokeninfo endpoint."""

    TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

    async def test_valid_token_openid_only(self, httpx_mock: HTTPXMock):
        """A token with only openid scope is accepted; client_id comes from 'sub'."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "sub": "12345",
                "scope": "openid",
                "expires_in": "3600",
            },
        )
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "12345"},
        )

        verifier = GoogleTokenVerifier()
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.client_id == "12345"
        assert result.scopes == ["openid"]
        assert result.expires_at is not None
        assert result.claims["sub"] == "12345"
        assert result.claims["aud"] == "123.apps.googleusercontent.com"

    async def test_valid_token_with_email_and_profile(self, httpx_mock: HTTPXMock):
        """A token with email+profile scope returns correct scopes and profile claims."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "sub": "12345",
                "email": "user@example.com",
                "email_verified": "true",
                "scope": "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile",
                "expires_in": "3600",
            },
        )
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={
                "sub": "12345",
                "email": "user@example.com",
                "verified_email": True,
                "name": "Test User",
                "picture": "https://example.com/photo.jpg",
                "given_name": "Test",
                "family_name": "User",
                "locale": "en",
            },
        )

        verifier = GoogleTokenVerifier()
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.client_id == "12345"
        assert "openid" in result.scopes
        assert "https://www.googleapis.com/auth/userinfo.email" in result.scopes
        assert "https://www.googleapis.com/auth/userinfo.profile" in result.scopes
        assert result.claims["email"] == "user@example.com"
        assert result.claims["name"] == "Test User"
        assert result.claims["picture"] == "https://example.com/photo.jpg"

    async def test_expired_or_invalid_token_returns_none(self, httpx_mock: HTTPXMock):
        """HTTP 400 from tokeninfo endpoint causes verify_token to return None."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            status_code=400,
            json={
                "error": "invalid_token",
                "error_description": "Token has been expired or revoked.",
            },
        )

        verifier = GoogleTokenVerifier()
        result = await verifier.verify_token("expired-token")

        assert result is None

    async def test_missing_aud_returns_none(self, httpx_mock: HTTPXMock):
        """A 200 response without 'aud' is rejected."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={"sub": "12345", "scope": "openid", "expires_in": "3600"},
        )

        verifier = GoogleTokenVerifier()
        result = await verifier.verify_token("token-without-aud")

        assert result is None

    async def test_missing_sub_returns_none(self, httpx_mock: HTTPXMock):
        """A 200 response without 'sub' is rejected."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "scope": "openid",
                "expires_in": "3600",
            },
        )

        verifier = GoogleTokenVerifier()
        result = await verifier.verify_token("token-without-sub")

        assert result is None

    async def test_required_scopes_satisfied(self, httpx_mock: HTTPXMock):
        """Token with required scopes passes the scope check."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "sub": "12345",
                "email": "user@example.com",
                "scope": "openid https://www.googleapis.com/auth/userinfo.email",
                "expires_in": "3600",
            },
        )
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "12345", "email": "user@example.com"},
        )

        verifier = GoogleTokenVerifier(
            required_scopes=["openid", "email"],
        )
        result = await verifier.verify_token("valid-token")

        assert result is not None

    async def test_required_scopes_not_satisfied_returns_none(
        self, httpx_mock: HTTPXMock
    ):
        """Token without required scopes is rejected."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "sub": "12345",
                "scope": "openid",
                "expires_in": "3600",
            },
        )

        verifier = GoogleTokenVerifier(
            required_scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
            ],
        )
        result = await verifier.verify_token("token-missing-email-scope")

        assert result is None

    async def test_uses_query_param_not_bearer_header(self, httpx_mock: HTTPXMock):
        """verify_token sends the token as a query parameter to tokeninfo, not a Bearer header."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "sub": "12345",
                "scope": "openid",
                "expires_in": "3600",
            },
        )
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "12345"},
        )

        verifier = GoogleTokenVerifier()
        await verifier.verify_token("my-access-token")

        requests = httpx_mock.get_requests()
        tokeninfo_req = requests[0]
        assert "access_token=my-access-token" in str(tokeninfo_req.url)
        assert "Authorization" not in tokeninfo_req.headers

    async def test_calls_tokeninfo_endpoint(self, httpx_mock: HTTPXMock):
        """verify_token calls the tokeninfo endpoint, not the userinfo endpoint, for verification."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "sub": "12345",
                "scope": "openid",
                "expires_in": "3600",
            },
        )
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "12345"},
        )

        verifier = GoogleTokenVerifier()
        await verifier.verify_token("valid-token")

        requests = httpx_mock.get_requests()
        assert len(requests) >= 1
        assert "tokeninfo" in str(requests[0].url)
        assert "oauth2.googleapis.com" in str(requests[0].url)

    async def test_expires_at_computed_from_expires_in(self, httpx_mock: HTTPXMock):
        """expires_at is set from expires_in returned by tokeninfo."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "sub": "12345",
                "scope": "openid",
                "expires_in": "3600",
            },
        )
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "12345"},
        )

        before = int(time.time())
        verifier = GoogleTokenVerifier()
        result = await verifier.verify_token("valid-token")
        after = int(time.time())

        assert result is not None
        assert result.expires_at is not None
        assert before + 3600 <= result.expires_at <= after + 3600

    async def test_profile_data_fetched_from_userinfo(self, httpx_mock: HTTPXMock):
        """Profile data (name, picture, locale) comes from the v2 userinfo endpoint."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "sub": "12345",
                "scope": "openid https://www.googleapis.com/auth/userinfo.profile",
                "expires_in": "3600",
            },
        )
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={
                "sub": "12345",
                "name": "Test User",
                "picture": "https://example.com/photo.jpg",
                "given_name": "Test",
                "family_name": "User",
                "locale": "en",
            },
        )

        verifier = GoogleTokenVerifier()
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert result.claims["name"] == "Test User"
        assert result.claims["picture"] == "https://example.com/photo.jpg"
        assert result.claims["given_name"] == "Test"
        assert result.claims["family_name"] == "User"
        assert result.claims["locale"] == "en"

    async def test_non_openid_scopes_checked_correctly(self, httpx_mock: HTTPXMock):
        """Calendar scope is correctly checked from the tokeninfo scope string."""
        httpx_mock.add_response(
            url=_TOKENINFO_RE,
            json={
                "aud": "123.apps.googleusercontent.com",
                "sub": "12345",
                "scope": "openid https://www.googleapis.com/auth/calendar",
                "expires_in": "3600",
            },
        )
        httpx_mock.add_response(
            url=_USERINFO_RE,
            json={"sub": "12345"},
        )

        verifier = GoogleTokenVerifier(
            required_scopes=["openid", "https://www.googleapis.com/auth/calendar"],
        )
        result = await verifier.verify_token("valid-token")

        assert result is not None
        assert "https://www.googleapis.com/auth/calendar" in result.scopes
