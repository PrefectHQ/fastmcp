"""Tests for Discord OAuth provider."""

from fastmcp.server.auth.providers.discord import DiscordProvider


class TestDiscordProvider:
    """Test Discord OAuth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test DiscordProvider initialization with explicit parameters."""
        provider = DiscordProvider(
            client_id="env_client_id",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            required_scopes=["email", "identify"],
            jwt_signing_key="test-secret",
        )

        assert provider._upstream_client_id == "env_client_id"
        assert provider._upstream_client_secret.get_secret_value() == "GOCSPX-test123"
        assert str(provider.base_url) == "https://myserver.com/"

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        provider = DiscordProvider(
            client_id="env_client_id",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )

        # Check defaults
        assert provider._redirect_path == "/auth/callback"

    def test_oauth_endpoints_configured_correctly(self):
        """Test that OAuth endpoints are configured correctly."""
        provider = DiscordProvider(
            client_id="env_client_id",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
        )

        # Check that endpoints use Discord's OAuth2 endpoints
        assert (
            provider._upstream_authorization_endpoint
            == "https://discord.com/oauth2/authorize"
        )
        assert (
            provider._upstream_token_endpoint == "https://discord.com/api/oauth2/token"
        )
        # Discord provider doesn't currently set a revocation endpoint
        assert provider._upstream_revocation_endpoint is None

    def test_discord_specific_scopes(self):
        """Test handling of Discord-specific scope formats."""
        # Just test that the provider accepts Discord-specific scopes without error
        provider = DiscordProvider(
            client_id="env_client_id",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            required_scopes=[
                "identify",
                "email",
            ],
            jwt_signing_key="test-secret",
        )

        # Provider should initialize successfully with these scopes
        assert provider is not None
