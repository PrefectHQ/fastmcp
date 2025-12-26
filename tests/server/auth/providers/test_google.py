"""Tests for Google OAuth provider."""

from fastmcp.server.auth.providers.google import GoogleProvider


class TestGoogleProvider:
    """Test Google OAuth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test GoogleProvider initialization with explicit parameters."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            required_scopes=["openid", "email", "profile"],
            jwt_signing_key="test-secret",
        )

        assert provider._upstream_client_id == "123456789.apps.googleusercontent.com"
        assert provider._upstream_client_secret.get_secret_value() == "GOCSPX-test123"
        assert str(provider.base_url) == "https://myserver.com/"

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )

        # Check defaults
        assert provider._redirect_path == "/auth/callback"
        # Google provider has ["openid"] as default but we can't easily verify without accessing internals

    def test_oauth_endpoints_configured_correctly(self):
        """Test that OAuth endpoints are configured correctly."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
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

    def test_google_specific_scopes(self):
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
        )

        # Provider should initialize successfully with these scopes
        assert provider is not None

    def test_extra_authorize_params_defaults(self):
        """Test that Google-specific defaults are set for refresh token support."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )

        # Should have Google-specific defaults for refresh token support
        assert provider._extra_authorize_params == {
            "access_type": "offline",
            "prompt": "consent",
        }

    def test_extra_authorize_params_override_defaults(self):
        """Test that user can override default extra authorize params."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            extra_authorize_params={"prompt": "select_account"},
        )

        # User override should replace the default
        assert provider._extra_authorize_params["prompt"] == "select_account"
        # But other defaults should remain
        assert provider._extra_authorize_params["access_type"] == "offline"

    def test_extra_authorize_params_add_new_params(self):
        """Test that user can add additional authorize params."""
        provider = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            extra_authorize_params={"login_hint": "user@example.com"},
        )

        # New param should be added
        assert provider._extra_authorize_params["login_hint"] == "user@example.com"
        # Defaults should still be present
        assert provider._extra_authorize_params["access_type"] == "offline"
        assert provider._extra_authorize_params["prompt"] == "consent"
