"""Unit tests for GitHub OAuth provider."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from key_value.aio.stores.memory import MemoryStore

from fastmcp.server.auth.providers.github import (
    GitHubProvider,
    GitHubTokenVerifier,
)


@pytest.fixture
def memory_storage() -> MemoryStore:
    """Provide a MemoryStore for tests to avoid SQLite initialization on Windows."""
    return MemoryStore()


class TestGitHubProvider:
    """Test GitHubProvider initialization."""

    def test_init_with_explicit_params(self, memory_storage: MemoryStore):
        """Test initialization with explicit parameters."""
        provider = GitHubProvider(
            client_id="test_client",
            client_secret="test_secret",
            base_url="https://example.com",
            redirect_path="/custom/callback",
            required_scopes=["user", "repo"],
            timeout_seconds=30,
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Check that the provider was initialized correctly
        assert provider._upstream_client_id == "test_client"
        assert provider._upstream_client_secret is not None
        assert provider._upstream_client_secret.get_secret_value() == "test_secret"
        assert (
            str(provider.base_url) == "https://example.com/"
        )  # URLs get normalized with trailing slash
        assert provider._redirect_path == "/custom/callback"

    def test_init_defaults(self, memory_storage: MemoryStore):
        """Test that default values are applied correctly."""
        provider = GitHubProvider(
            client_id="test_client",
            client_secret="test_secret",
            base_url="https://example.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Check defaults
        assert provider._redirect_path == "/auth/callback"
        # The required_scopes should be passed to the token verifier
        assert provider._token_validator.required_scopes == ["user"]

    def test_init_with_resource_base_url(self, memory_storage: MemoryStore):
        """Test that resource_base_url overrides the advertised protected resource."""
        provider = GitHubProvider(
            client_id="test_client",
            client_secret="test_secret",
            base_url="https://auth.example.com/proxy",
            resource_base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        provider.set_mcp_path("/mcp")

        assert str(provider.base_url) == "https://auth.example.com/proxy"
        assert str(provider.resource_base_url) == "https://api.example.com/"
        assert provider.jwt_issuer.audience == "https://api.example.com/mcp"


class TestGitHubTokenVerifier:
    """Test GitHubTokenVerifier."""

    def test_init_with_custom_scopes(self, memory_storage: MemoryStore):
        """Test initialization with custom required scopes."""
        verifier = GitHubTokenVerifier(
            required_scopes=["user", "repo"],
            timeout_seconds=30,
        )

        assert verifier.required_scopes == ["user", "repo"]
        assert verifier.timeout_seconds == 30

    def test_init_defaults(self, memory_storage: MemoryStore):
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


def _mock_github_success(mock_client: AsyncMock) -> None:
    """Configure *mock_client* to return a successful GitHub user + scopes response."""
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.return_value = {
        "id": 12345,
        "login": "testuser",
        "name": "Test User",
        "email": "test@example.com",
        "avatar_url": "https://github.com/testuser.png",
    }

    scopes_response = MagicMock()
    scopes_response.status_code = 200
    scopes_response.headers = {"x-oauth-scopes": "user,repo"}

    mock_client.get.side_effect = [user_response, scopes_response]


def _mock_github_failure(mock_client: AsyncMock) -> None:
    """Configure *mock_client* to return a 401 GitHub response."""
    fail_response = MagicMock()
    fail_response.status_code = 401
    fail_response.text = "Bad credentials"
    mock_client.get.return_value = fail_response


class TestGitHubTokenVerifierCaching:
    """Test caching behaviour on GitHubTokenVerifier."""

    def test_cache_disabled_by_default(self):
        verifier = GitHubTokenVerifier()
        assert not verifier._cache.enabled

    def test_cache_enabled_with_ttl(self):
        verifier = GitHubTokenVerifier(cache_ttl_seconds=300)
        assert verifier._cache.enabled

    async def test_cache_hit_avoids_second_api_call(self):
        verifier = GitHubTokenVerifier(
            required_scopes=["user"],
            cache_ttl_seconds=300,
        )

        mock_client = AsyncMock()

        with patch(
            "fastmcp.server.auth.providers.github.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client

            _mock_github_success(mock_client)
            result1 = await verifier.verify_token("tok-1")
            assert result1 is not None
            assert mock_client.get.call_count == 2  # /user + /user/repos

            result2 = await verifier.verify_token("tok-1")
            assert result2 is not None
            assert result2.client_id == result1.client_id
            assert mock_client.get.call_count == 2  # no additional calls

    async def test_cache_disabled_makes_every_call(self):
        verifier = GitHubTokenVerifier(
            required_scopes=["user"],
            cache_ttl_seconds=0,
        )

        mock_client = AsyncMock()

        with patch(
            "fastmcp.server.auth.providers.github.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client

            _mock_github_success(mock_client)
            await verifier.verify_token("tok-1")
            assert mock_client.get.call_count == 2

            _mock_github_success(mock_client)
            await verifier.verify_token("tok-1")
            assert mock_client.get.call_count == 4

    async def test_failures_are_not_cached(self):
        verifier = GitHubTokenVerifier(cache_ttl_seconds=300)

        mock_client = AsyncMock()

        with patch(
            "fastmcp.server.auth.providers.github.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client

            _mock_github_failure(mock_client)
            result1 = await verifier.verify_token("bad-tok")
            assert result1 is None

            _mock_github_success(mock_client)
            result2 = await verifier.verify_token("bad-tok")
            assert result2 is not None

    async def test_cached_result_is_defensive_copy(self):
        verifier = GitHubTokenVerifier(
            required_scopes=["user"],
            cache_ttl_seconds=300,
        )

        mock_client = AsyncMock()

        with patch(
            "fastmcp.server.auth.providers.github.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client

            _mock_github_success(mock_client)
            result1 = await verifier.verify_token("tok-1")
            assert result1 is not None
            result1.claims["login"] = "MUTATED"

            result2 = await verifier.verify_token("tok-1")
            assert result2 is not None
            assert result2.claims["login"] == "testuser"

    async def test_scope_failure_skips_cache(self):
        """Token verified with fallback scopes (scope API failed) should not be cached."""
        verifier = GitHubTokenVerifier(cache_ttl_seconds=300)

        mock_client = AsyncMock()

        user_response = MagicMock()
        user_response.status_code = 200
        user_response.json.return_value = {
            "id": 12345,
            "login": "testuser",
            "name": "Test User",
            "email": "test@example.com",
            "avatar_url": "https://github.com/testuser.png",
        }

        scopes_response = MagicMock()
        scopes_response.status_code = 500
        scopes_response.headers = {}

        with patch(
            "fastmcp.server.auth.providers.github.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client

            mock_client.get.side_effect = [user_response, scopes_response]
            result = await verifier.verify_token("tok-1")
            assert result is not None
            # Should NOT be cached because scope response was not 200
            assert not verifier._cache.enabled or len(verifier._cache._entries) == 0

    def test_provider_passes_cache_params(self, memory_storage: MemoryStore):
        provider = GitHubProvider(
            client_id="cid",
            client_secret="csec",
            base_url="https://example.com",
            cache_ttl_seconds=120,
            max_cache_size=500,
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )
        verifier = provider._token_validator
        assert isinstance(verifier, GitHubTokenVerifier)
        assert verifier._cache.enabled
        assert verifier._cache._ttl == 120
        assert verifier._cache._max_size == 500
