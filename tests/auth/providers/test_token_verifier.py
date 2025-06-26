"""Tests for TokenVerifier protocol implementation in auth providers."""

import pytest
from mcp.server.auth.provider import AccessToken

from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider


class TestBearerAuthProviderTokenVerifier:
    """Test that BearerAuthProvider implements TokenVerifier protocol correctly."""

    @pytest.fixture
    def rsa_key_pair(self) -> RSAKeyPair:
        """Generate RSA key pair for testing."""
        return RSAKeyPair.generate()

    @pytest.fixture
    def bearer_provider(self, rsa_key_pair: RSAKeyPair) -> BearerAuthProvider:
        """Create BearerAuthProvider for testing."""
        return BearerAuthProvider(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

    @pytest.fixture
    def valid_token(self, rsa_key_pair: RSAKeyPair) -> str:
        """Create a valid test token."""
        return rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            scopes=["read", "write"],
        )

    @pytest.fixture
    def expired_token(self, rsa_key_pair: RSAKeyPair) -> str:
        """Create an expired test token."""
        return rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            expires_in_seconds=-3600,  # Expired 1 hour ago
        )

    async def test_verify_token_with_valid_token(
        self, bearer_provider: BearerAuthProvider, valid_token: str
    ):
        """Test that verify_token returns AccessToken for valid token."""
        result = await bearer_provider.verify_token(valid_token)

        assert result is not None
        assert isinstance(result, AccessToken)
        assert result.token == valid_token
        assert result.client_id == "test-user"
        assert "read" in result.scopes
        assert "write" in result.scopes

    async def test_verify_token_with_expired_token(
        self, bearer_provider: BearerAuthProvider, expired_token: str
    ):
        """Test that verify_token returns None for expired token."""
        result = await bearer_provider.verify_token(expired_token)
        assert result is None

    async def test_verify_token_with_invalid_token(
        self, bearer_provider: BearerAuthProvider
    ):
        """Test that verify_token returns None for invalid token."""
        result = await bearer_provider.verify_token("invalid.token.here")
        assert result is None

    async def test_verify_token_with_malformed_token(
        self, bearer_provider: BearerAuthProvider
    ):
        """Test that verify_token returns None for malformed token."""
        result = await bearer_provider.verify_token("not-a-jwt")
        assert result is None

    async def test_verify_token_delegation_to_load_access_token(
        self, bearer_provider: BearerAuthProvider, valid_token: str
    ):
        """Test that verify_token delegates to load_access_token."""
        # Both methods should return the same result
        verify_result = await bearer_provider.verify_token(valid_token)
        load_result = await bearer_provider.load_access_token(valid_token)

        assert verify_result == load_result
        if verify_result is not None and load_result is not None:
            assert verify_result.token == load_result.token
            assert verify_result.client_id == load_result.client_id
            assert verify_result.scopes == load_result.scopes


class TestInMemoryOAuthProviderTokenVerifier:
    """Test that InMemoryOAuthProvider implements TokenVerifier protocol correctly."""

    @pytest.fixture
    def in_memory_provider(self) -> InMemoryOAuthProvider:
        """Create InMemoryOAuthProvider for testing."""
        return InMemoryOAuthProvider(
            issuer_url="https://test.example.com",
            required_scopes=["user"],
        )

    async def test_verify_token_with_nonexistent_token(
        self, in_memory_provider: InMemoryOAuthProvider
    ):
        """Test that verify_token returns None for nonexistent token."""
        result = await in_memory_provider.verify_token("nonexistent-token")
        assert result is None

    async def test_verify_token_delegation_to_load_access_token(
        self, in_memory_provider: InMemoryOAuthProvider
    ):
        """Test that verify_token delegates to load_access_token."""
        # Create a test token in the provider's storage
        test_token = "test-access-token"
        test_access_token = AccessToken(
            token=test_token,
            client_id="test-client",
            scopes=["user"],
            expires_at=None,  # No expiry
        )
        in_memory_provider.access_tokens[test_token] = test_access_token

        # Both methods should return the same result
        verify_result = await in_memory_provider.verify_token(test_token)
        load_result = await in_memory_provider.load_access_token(test_token)

        assert verify_result == load_result
        assert verify_result is not None
        assert verify_result.token == test_token
        assert verify_result.client_id == "test-client"
        assert verify_result.scopes == ["user"]

    async def test_verify_token_with_expired_token(
        self, in_memory_provider: InMemoryOAuthProvider
    ):
        """Test that verify_token returns None for expired token."""
        import time

        # Create an expired token
        expired_token = "expired-token"
        expired_access_token = AccessToken(
            token=expired_token,
            client_id="test-client",
            scopes=["user"],
            expires_at=int(time.time()) - 3600,  # Expired 1 hour ago
        )
        in_memory_provider.access_tokens[expired_token] = expired_access_token

        result = await in_memory_provider.verify_token(expired_token)
        assert result is None

        # Token should be cleaned up from storage
        assert expired_token not in in_memory_provider.access_tokens


class TestTokenVerifierProtocolCompliance:
    """Test that our providers properly implement the TokenVerifier protocol."""

    async def test_bearer_provider_implements_protocol(self):
        """Test that BearerAuthProvider can be used as TokenVerifier."""
        key_pair = RSAKeyPair.generate()
        provider = BearerAuthProvider(public_key=key_pair.public_key)

        # Should have the required method for TokenVerifier protocol
        assert hasattr(provider, "verify_token")
        assert callable(provider.verify_token)

    async def test_in_memory_provider_implements_protocol(self):
        """Test that InMemoryOAuthProvider can be used as TokenVerifier."""
        provider = InMemoryOAuthProvider()

        # Should have the required method for TokenVerifier protocol
        assert hasattr(provider, "verify_token")
        assert callable(provider.verify_token)
