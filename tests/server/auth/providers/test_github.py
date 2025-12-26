"""Unit tests for GitHub OAuth provider."""

from unittest.mock import MagicMock, patch

from fastmcp.server.auth.providers.github import (
    GitHubProvider,
    GitHubTokenVerifier,
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

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        provider = GitHubProvider(
            client_id="test_client",
            client_secret="test_secret",
            base_url="https://example.com",
            jwt_signing_key="test-secret",
        )

        # Check defaults
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
