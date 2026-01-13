"""Tests for BearerAuthBackend integration with TokenVerifier."""

import pytest
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from starlette.requests import HTTPConnection

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair


class TestBearerAuthBackendTokenVerifierIntegration:
    """Test BearerAuthBackend works with TokenVerifier protocol."""

    @pytest.fixture
    def rsa_key_pair(self) -> RSAKeyPair:
        """Generate RSA key pair for testing."""
        return RSAKeyPair.generate()

    @pytest.fixture
    def jwt_verifier(self, rsa_key_pair: RSAKeyPair) -> JWTVerifier:
        """Create JWTVerifier for testing."""
        return JWTVerifier(
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

    def test_bearer_auth_backend_constructor_accepts_token_verifier(
        self, jwt_verifier: JWTVerifier
    ):
        """Test that BearerAuthBackend constructor accepts TokenVerifier."""
        # This should not raise an error
        backend = BearerAuthBackend(jwt_verifier)
        assert isinstance(backend.token_verifier, TokenVerifier)
        assert backend.token_verifier is jwt_verifier

    async def test_bearer_auth_backend_authenticate_with_valid_token(
        self, jwt_verifier: JWTVerifier, valid_token: str
    ):
        """Test BearerAuthBackend authentication with valid token."""
        backend = BearerAuthBackend(jwt_verifier)

        # Create mock HTTPConnection with Authorization header
        scope = {
            "type": "http",
            "headers": [(b"authorization", f"Bearer {valid_token}".encode())],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)

        assert result is not None
        credentials, user = result
        assert credentials.scopes == ["read", "write"]
        assert user.username == "test-user"
        assert hasattr(user, "access_token")
        assert user.access_token.token == valid_token

    async def test_bearer_auth_backend_authenticate_with_invalid_token(
        self, jwt_verifier: JWTVerifier
    ):
        """Test BearerAuthBackend authentication with invalid token."""
        backend = BearerAuthBackend(jwt_verifier)

        # Create mock HTTPConnection with invalid Authorization header
        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer invalid-token")],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)
        assert result is None

    async def test_bearer_auth_backend_authenticate_with_no_header(
        self, jwt_verifier: JWTVerifier
    ):
        """Test BearerAuthBackend authentication with no Authorization header."""
        backend = BearerAuthBackend(jwt_verifier)

        # Create mock HTTPConnection without Authorization header
        scope = {
            "type": "http",
            "headers": [],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)
        assert result is None

    async def test_bearer_auth_backend_authenticate_with_non_bearer_token(
        self, jwt_verifier: JWTVerifier
    ):
        """Test BearerAuthBackend authentication with non-Bearer token."""
        backend = BearerAuthBackend(jwt_verifier)

        # Create mock HTTPConnection with Basic auth header
        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Basic dXNlcjpwYXNz")],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)
        assert result is None


class MockTokenVerifier:
    """Mock TokenVerifier for testing backend integration."""

    def __init__(self, return_value: AccessToken | None = None):
        self.return_value = return_value
        self.verify_token_calls = []

    async def verify_token(self, token: str) -> AccessToken | None:
        """Mock verify_token method."""
        self.verify_token_calls.append(token)
        return self.return_value


class TestBearerAuthBackendWithMockVerifier:
    """Test BearerAuthBackend with mock TokenVerifier."""

    async def test_backend_calls_verify_token_method(self):
        """Test that BearerAuthBackend calls verify_token on the verifier."""
        mock_access_token = AccessToken(
            token="test-token",
            client_id="test-client",
            scopes=["read"],
            expires_at=None,
        )
        mock_verifier = MockTokenVerifier(return_value=mock_access_token)
        backend = BearerAuthBackend(mock_verifier)

        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer test-token")],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)

        # Should have called verify_token with the token
        assert mock_verifier.verify_token_calls == ["test-token"]

        # Should return authentication result
        assert result is not None
        credentials, user = result
        assert credentials.scopes == ["read"]
        assert user.username == "test-client"

    async def test_backend_handles_verify_token_none_result(self):
        """Test that BearerAuthBackend handles None result from verify_token."""
        mock_verifier = MockTokenVerifier(return_value=None)
        backend = BearerAuthBackend(mock_verifier)

        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer invalid-token")],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)

        # Should have called verify_token
        assert mock_verifier.verify_token_calls == ["invalid-token"]

        # Should return None for authentication failure
        assert result is None
