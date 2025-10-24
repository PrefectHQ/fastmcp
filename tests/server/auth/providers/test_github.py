"""Unit tests for GitHub OAuth provider."""

import os
from unittest.mock import MagicMock, patch

import pytest

from fastmcp.server.auth.providers.github import (
    GitHubProvider,
    GitHubProviderSettings,
    GitHubTokenVerifier,
)


class TestGitHubProviderSettings:
    """Test settings for GitHub OAuth provider."""

    def test_settings_from_env_vars(self):
        """Test that settings can be loaded from environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID": "env_client_id",
                "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET": "env_secret",
                "FASTMCP_SERVER_AUTH_GITHUB_BASE_URL": "https://example.com",
                "FASTMCP_SERVER_AUTH_GITHUB_REDIRECT_PATH": "/custom/callback",
                "FASTMCP_SERVER_AUTH_GITHUB_TIMEOUT_SECONDS": "30",
                "FASTMCP_SERVER_AUTH_GITHUB_JWT_SIGNING_KEY": "test-secret",
            },
        ):
            settings = GitHubProviderSettings()

            assert settings.client_id == "env_client_id"
            assert (
                settings.client_secret
                and settings.client_secret.get_secret_value() == "env_secret"
            )
            assert settings.base_url == "https://example.com"
            assert settings.redirect_path == "/custom/callback"
            assert settings.timeout_seconds == 30

    def test_settings_explicit_override_env(self):
        """Test that explicit settings override environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID": "env_client_id",
                "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET": "env_secret",
            },
        ):
            settings = GitHubProviderSettings.model_validate(
                {
                    "client_id": "explicit_client_id",
                    "client_secret": "explicit_secret",
                    "jwt_signing_key": "test-secret",
                }
            )

            assert settings.client_id == "explicit_client_id"
            assert (
                settings.client_secret
                and settings.client_secret.get_secret_value() == "explicit_secret"
            )


class TestGitHubProvider:
    """Test GitHubProvider initialization."""

    def test_init_with_explicit_params(self):
        """Test initialization with explicit parameters."""
        provider = GitHubProvider(
            client_id="test_client",
            client_secret="test_secret",
            base_url="https://example.com",
            redirect_path="/custom/callback",
            required_scopes=["user", "repo"],
            timeout_seconds=30,
            jwt_signing_key="test-secret",
        )

        # Check that the provider was initialized correctly
        assert provider._upstream_client_id == "test_client"
        assert provider._upstream_client_secret.get_secret_value() == "test_secret"
        assert (
            str(provider.base_url) == "https://example.com/"
        )  # URLs get normalized with trailing slash
        assert provider._redirect_path == "/custom/callback"

    @pytest.mark.parametrize(
        "scopes_env",
        [
            "user,repo",
            '["user", "repo"]',
        ],
    )
    def test_init_with_env_vars(self, scopes_env):
        """Test initialization with environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID": "env_client_id",
                "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET": "env_secret",
                "FASTMCP_SERVER_AUTH_GITHUB_BASE_URL": "https://env-example.com",
                "FASTMCP_SERVER_AUTH_GITHUB_REQUIRED_SCOPES": scopes_env,
                "FASTMCP_SERVER_AUTH_GITHUB_JWT_SIGNING_KEY": "test-secret",
            },
        ):
            provider = GitHubProvider()

            assert provider._upstream_client_id == "env_client_id"
            assert provider._upstream_client_secret.get_secret_value() == "env_secret"
            assert str(provider.base_url) == "https://env-example.com/"
            assert provider._token_validator.required_scopes == ["user", "repo"]

    def test_init_explicit_overrides_env(self):
        """Test that explicit parameters override environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID": "env_client_id",
                "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET": "env_secret",
                "FASTMCP_SERVER_AUTH_GITHUB_JWT_SIGNING_KEY": "test-secret",
            },
        ):
            provider = GitHubProvider(
                client_id="explicit_client",
                client_secret="explicit_secret",
                jwt_signing_key="test-secret",
            )

            assert provider._upstream_client_id == "explicit_client"
            assert (
                provider._upstream_client_secret.get_secret_value() == "explicit_secret"
            )

    def test_init_missing_client_id_raises_error(self):
        """Test that missing client_id raises ValueError."""
        # Clear environment variables to test proper error handling
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="client_id is required"):
                GitHubProvider(client_secret="test_secret")

    def test_init_missing_client_secret_raises_error(self):
        """Test that missing client_secret raises ValueError."""
        # Clear environment variables to test proper error handling
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="client_secret is required"):
                GitHubProvider(client_id="test_client")

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        provider = GitHubProvider(
            client_id="test_client",
            client_secret="test_secret",
            jwt_signing_key="test-secret",
        )

        # Check defaults
        assert provider.base_url is None
        assert provider._redirect_path == "/auth/callback"
        # The required_scopes should be passed to the token verifier
        assert provider._token_validator.required_scopes == ["user"]


class TestGitHubTokenVerifier:
    """Test GitHubTokenVerifier."""

    def test_init_with_custom_scopes(self):
        """Test initialization with custom required scopes."""
        verifier = GitHubTokenVerifier(
            required_scopes=["user", "repo"],
            timeout_seconds=30,
        )

        assert verifier.required_scopes == ["user", "repo"]
        assert verifier.timeout_seconds == 30

    def test_init_defaults(self):
        """Test initialization with defaults."""
        verifier = GitHubTokenVerifier()

        assert (
            verifier.required_scopes == []
        )  # Parent TokenVerifier sets empty list as default
        assert verifier.timeout_seconds == 10

    async def test_verify_token_github_api_failure(self):
        """Test token verification when GitHub API returns error."""
        verifier = GitHubTokenVerifier()

        # Mock httpx.AsyncClient to simulate GitHub API failure
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Simulate 401 response from GitHub
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Bad credentials"
            mock_client.get.return_value = mock_response

            result = await verifier.verify_token("invalid_token")
            assert result is None

    async def test_verify_token_success(self):
        """Test successful token verification."""
        from unittest.mock import AsyncMock

        verifier = GitHubTokenVerifier(required_scopes=["user"])

        # Mock the httpx.AsyncClient directly
        mock_client = AsyncMock()

        # Mock successful user API response
        user_response = MagicMock()
        user_response.status_code = 200
        user_response.json.return_value = {
            "id": 12345,
            "login": "testuser",
            "name": "Test User",
            "email": "test@example.com",
            "avatar_url": "https://github.com/testuser.png",
        }

        # Mock successful scopes API response
        scopes_response = MagicMock()
        scopes_response.headers = {"x-oauth-scopes": "user,repo"}

        # Set up the mock client to return our responses
        mock_client.get.side_effect = [user_response, scopes_response]

        # Patch the AsyncClient context manager
        with patch(
            "fastmcp.server.auth.providers.github.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await verifier.verify_token("valid_token")

            assert result is not None
            assert result.token == "valid_token"
            assert result.client_id == "12345"
            assert result.scopes == ["user", "repo"]
            assert result.claims["login"] == "testuser"
            assert result.claims["name"] == "Test User"
