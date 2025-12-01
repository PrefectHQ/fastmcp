"""Tests for Discord OAuth provider."""

import os
from unittest.mock import patch

import pytest

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

    @pytest.mark.parametrize(
        "scopes_env",
        [
            "identify,email",
            '["identify", "email"]',
        ],
    )
    def test_init_with_env_vars(self, scopes_env):
        """Test DiscordProvider initialization from environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_DISCORD_CLIENT_ID": "env_client_id",
                "FASTMCP_SERVER_AUTH_DISCORD_CLIENT_SECRET": "GOCSPX-env456",
                "FASTMCP_SERVER_AUTH_DISCORD_BASE_URL": "https://envserver.com",
                "FASTMCP_SERVER_AUTH_DISCORD_REQUIRED_SCOPES": scopes_env,
                "FASTMCP_SERVER_AUTH_DISCORD_JWT_SIGNING_KEY": "test-secret",
            },
        ):
            provider = DiscordProvider()

            assert provider._upstream_client_id == "env_client_id"
            assert (
                provider._upstream_client_secret.get_secret_value() == "GOCSPX-env456"
            )
            assert str(provider.base_url) == "https://envserver.com/"
            assert provider._token_validator.required_scopes == [
                "identify",
                "email",
            ]

    def test_init_missing_client_id_raises_error(self):
        """Test that missing client_id raises ValueError."""
        # Clear environment variables to test proper error handling
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="client_id is required"):
                DiscordProvider(client_secret="GOCSPX-test123")

    def test_init_missing_client_secret_raises_error(self):
        """Test that missing client_secret raises ValueError."""
        # Clear environment variables to test proper error handling
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="client_secret is required"):
                DiscordProvider(client_id="env_client_id")

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        provider = DiscordProvider(
            client_id="env_client_id",
            client_secret="GOCSPX-test123",
            jwt_signing_key="test-secret",
        )

        # Check defaults
        assert provider.base_url is None
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
            required_scopes=[
                "identify",
                "email",
            ],
            jwt_signing_key="test-secret",
        )

        # Provider should initialize successfully with these scopes
        assert provider is not None
