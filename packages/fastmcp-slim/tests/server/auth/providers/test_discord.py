"""Tests for Discord OAuth provider."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from key_value.aio.stores.memory import MemoryStore

from fastmcp.server.auth.providers.discord import DiscordProvider, DiscordTokenVerifier


@pytest.fixture
def memory_storage() -> MemoryStore:
    """Provide a MemoryStore for tests to avoid SQLite initialization on Windows."""
    return MemoryStore()


class TestDiscordProvider:
    """Test Discord OAuth provider functionality."""

    def test_init_with_explicit_params(self, memory_storage: MemoryStore):
        """Test DiscordProvider initialization with explicit parameters."""
        provider = DiscordProvider(
            client_id="env_client_id",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            required_scopes=["email", "identify"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._upstream_client_id == "env_client_id"
        assert provider._upstream_client_secret is not None
        assert provider._upstream_client_secret.get_secret_value() == "GOCSPX-test123"
        assert str(provider.base_url) == "https://myserver.com/"

    def test_init_defaults(self, memory_storage: MemoryStore):
        """Test that default values are applied correctly."""
        provider = DiscordProvider(
            client_id="env_client_id",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Check defaults
        assert provider._redirect_path == "/auth/callback"

    def test_oauth_endpoints_configured_correctly(self, memory_storage: MemoryStore):
        """Test that OAuth endpoints are configured correctly."""
        provider = DiscordProvider(
            client_id="env_client_id",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
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

    def test_discord_specific_scopes(self, memory_storage: MemoryStore):
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
            client_storage=memory_storage,
        )

        # Provider should initialize successfully with these scopes
        assert provider is not None

    def test_token_verifier_is_bound_to_provider_client_id(
        self, memory_storage: MemoryStore
    ):
        """Test DiscordProvider binds token verifier to the configured client ID."""
        provider = DiscordProvider(
            client_id="expected-client-id",
            client_secret="GOCSPX-test123",
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        verifier = provider._token_validator
        assert isinstance(verifier, DiscordTokenVerifier)
        assert verifier.expected_client_id == "expected-client-id"


class TestDiscordTokenVerifier:
    """Test DiscordTokenVerifier behavior."""

    async def test_rejects_token_from_different_discord_application(self):
        """Token must be bound to configured Discord client_id."""
        verifier = DiscordTokenVerifier(expected_client_id="expected-app-id")

        mock_client = AsyncMock()
        token_info_response = MagicMock()
        token_info_response.status_code = 200
        token_info_response.json.return_value = {
            "application": {"id": "different-app-id"},
            "user": {"id": "123"},
            "scopes": ["identify"],
        }
        mock_client.get.return_value = token_info_response

        with patch(
            "fastmcp.server.auth.providers.discord.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client_class.return_value.__aenter__.return_value = mock_client
            result = await verifier.verify_token("token")

        assert result is None
