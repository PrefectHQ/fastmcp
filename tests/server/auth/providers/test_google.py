"""Tests for Google OAuth provider."""

import pytest
from key_value.aio.stores.memory import MemoryStore

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
