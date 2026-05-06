"""Tests for CustomHeaderAuthBackend."""

from starlette.requests import HTTPConnection

from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.auth import CustomHeaderAuthBackend


class MockTokenVerifier:
    """Mock TokenVerifier for testing backend integration."""

    def __init__(self, return_value: AccessToken | None = None):
        self.return_value = return_value
        self.verify_token_calls = []

    async def verify_token(self, token: str) -> AccessToken | None:
        """Mock verify_token method."""
        self.verify_token_calls.append(token)
        return self.return_value


class TestCustomHeaderAuthBackend:
    """Test CustomHeaderAuthBackend reads tokens from configurable headers."""

    def test_constructor_stores_header_name(self):
        """Backend stores the provided header name (lower-cased)."""
        verifier = MockTokenVerifier()
        backend = CustomHeaderAuthBackend(verifier, "X-Auth-Id")
        assert backend.token_verifier is verifier
        assert backend.header_name == "x-auth-id"

    async def test_authenticate_reads_token_from_custom_header(self):
        """Backend extracts the token value from the configured header."""
        mock_token = AccessToken(
            token="token-value",
            client_id="test-client",
            scopes=["read"],
            expires_at=None,
        )
        verifier = MockTokenVerifier(return_value=mock_token)
        backend = CustomHeaderAuthBackend(verifier, "x-auth-id")

        scope = {
            "type": "http",
            "headers": [(b"x-auth-id", b"token-value")],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)

        assert result is not None
        credentials, user = result
        assert credentials.scopes == ["read"]
        assert user.username == "test-client"

    async def test_authenticate_ignores_authorization_header(self):
        """Backend ignores 'authorization' when configured for a custom header."""
        mock_token = AccessToken(
            token="correct",
            client_id="test-client",
            scopes=["read"],
            expires_at=None,
        )
        verifier = MockTokenVerifier(return_value=mock_token)
        backend = CustomHeaderAuthBackend(verifier, "x-api-key")

        scope = {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer wrong-token"),
                (b"x-api-key", b"correct"),
            ],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)

        # The verifier should only see "correct" — not the Bearer token
        assert verifier.verify_token_calls == ["correct"]
        assert result is not None

    async def test_authenticate_returns_none_when_header_missing(self):
        """Backend returns None when the configured header is absent."""
        verifier = MockTokenVerifier()
        backend = CustomHeaderAuthBackend(verifier, "x-auth-id")

        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer present")],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)
        assert result is None

    async def test_authenticate_calls_verify_token_with_raw_value(self):
        """Backend passes the raw header value to verify_token (no prefix stripping)."""
        verifier = MockTokenVerifier()
        backend = CustomHeaderAuthBackend(verifier, "x-auth-id")

        scope = {
            "type": "http",
            "headers": [(b"x-auth-id", b"raw-token-value")],
        }
        conn = HTTPConnection(scope)

        await backend.authenticate(conn)

        assert verifier.verify_token_calls == ["raw-token-value"]

    async def test_authenticate_handles_verify_token_none_result(self):
        """Backend returns None when verify_token returns None."""
        verifier = MockTokenVerifier(return_value=None)
        backend = CustomHeaderAuthBackend(verifier, "x-auth-id")

        scope = {
            "type": "http",
            "headers": [(b"x-auth-id", b"bad-token")],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)

        assert verifier.verify_token_calls == ["bad-token"]
        assert result is None
