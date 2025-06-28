"""Tests for authentication setup in HTTP apps."""

import pytest
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from mcp.server.auth.provider import AccessToken
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware

from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.server.http import setup_auth_middleware_and_routes


class TestSetupAuthMiddlewareAndRoutes:
    """Test setup_auth_middleware_and_routes with TokenVerifier providers."""

    @pytest.fixture
    def bearer_provider(self) -> BearerAuthProvider:
        """Create BearerAuthProvider for testing."""
        key_pair = RSAKeyPair.generate()
        return BearerAuthProvider(
            public_key=key_pair.public_key,
            issuer="https://test.example.com",
            audience="https://api.example.com",
            required_scopes=["read", "write"],
        )

    @pytest.fixture
    def in_memory_provider(self) -> InMemoryOAuthProvider:
        """Create InMemoryOAuthProvider for testing."""
        return InMemoryOAuthProvider(
            issuer_url="https://test.example.com",
            required_scopes=["user"],
        )

    def test_setup_with_bearer_provider(self, bearer_provider: BearerAuthProvider):
        """Test that setup works with BearerAuthProvider as TokenVerifier."""
        middleware, auth_routes, required_scopes = setup_auth_middleware_and_routes(
            bearer_provider
        )

        # Should return middleware list
        assert isinstance(middleware, list)
        assert len(middleware) == 2  # AuthenticationMiddleware + AuthContextMiddleware

        # First middleware should be AuthenticationMiddleware with BearerAuthBackend
        auth_middleware = middleware[0]
        assert isinstance(auth_middleware, Middleware)
        assert auth_middleware.cls == AuthenticationMiddleware
        assert "backend" in auth_middleware.kwargs

        backend = auth_middleware.kwargs["backend"]
        assert isinstance(backend, BearerAuthBackend)
        assert backend.token_verifier is bearer_provider  # type: ignore[attr-defined]

        # Should return auth routes
        assert isinstance(auth_routes, list)
        assert len(auth_routes) > 0  # Should have OAuth routes

        # Should return required scopes
        assert required_scopes == ["read", "write"]

    def test_setup_with_in_memory_provider(
        self, in_memory_provider: InMemoryOAuthProvider
    ):
        """Test that setup works with InMemoryOAuthProvider as TokenVerifier."""
        middleware, auth_routes, required_scopes = setup_auth_middleware_and_routes(
            in_memory_provider
        )

        # Should return middleware list
        assert isinstance(middleware, list)
        assert len(middleware) == 2

        # Backend should use the provider as token verifier
        auth_middleware = middleware[0]
        backend = auth_middleware.kwargs["backend"]
        assert isinstance(backend, BearerAuthBackend)
        assert backend.token_verifier is in_memory_provider  # type: ignore[attr-defined]

        # Should return required scopes
        assert required_scopes == ["user"]

    def test_setup_preserves_provider_functionality(
        self, bearer_provider: BearerAuthProvider
    ):
        """Test that setup doesn't break the provider's functionality."""
        # Setup should not modify the provider
        original_issuer = bearer_provider.issuer
        original_scopes = bearer_provider.required_scopes

        middleware, auth_routes, required_scopes = setup_auth_middleware_and_routes(
            bearer_provider
        )

        # Provider should be unchanged
        assert bearer_provider.issuer == original_issuer
        assert bearer_provider.required_scopes == original_scopes

        # Provider should still work as TokenVerifier
        assert hasattr(bearer_provider, "verify_token")
        assert callable(bearer_provider.verify_token)


class MockOAuthProvider:
    """Mock OAuth provider that implements TokenVerifier."""

    def __init__(self, required_scopes=None, issuer_url="http://localhost:8000"):
        from pydantic import AnyHttpUrl

        from fastmcp.server.auth.auth import (
            ClientRegistrationOptions,
            RevocationOptions,
        )

        self.required_scopes = required_scopes or []
        self.issuer_url = AnyHttpUrl(issuer_url)
        self.service_documentation_url = None
        self.client_registration_options = ClientRegistrationOptions(enabled=False)
        self.revocation_options = RevocationOptions(enabled=False)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Mock verify_token implementation."""
        if token == "valid-token":
            return AccessToken(
                token=token,
                client_id="mock-client",
                scopes=self.required_scopes,
                expires_at=None,
            )
        return None


class TestSetupWithMockProvider:
    """Test setup function with mock provider."""

    def test_setup_with_mock_token_verifier(self):
        """Test that setup works with any TokenVerifier implementation."""
        mock_provider = MockOAuthProvider(required_scopes=["mock-scope"])

        middleware, auth_routes, required_scopes = setup_auth_middleware_and_routes(
            mock_provider  # type: ignore[arg-type]
        )

        # Should work with any TokenVerifier
        assert len(middleware) == 2
        auth_middleware = middleware[0]
        backend = auth_middleware.kwargs["backend"]
        assert isinstance(backend, BearerAuthBackend)
        assert backend.token_verifier is mock_provider  # type: ignore[attr-defined]

        assert required_scopes == ["mock-scope"]

    async def test_setup_middleware_can_authenticate(self):
        """Test that the setup middleware can actually authenticate requests."""
        mock_provider = MockOAuthProvider()

        middleware, _, _ = setup_auth_middleware_and_routes(mock_provider)  # type: ignore[arg-type]

        # Extract the BearerAuthBackend
        auth_middleware = middleware[0]
        backend = auth_middleware.kwargs["backend"]

        # Test authentication with valid token
        from starlette.requests import HTTPConnection

        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer valid-token")],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)  # type: ignore[attr-defined]
        assert result is not None

        credentials, user = result
        assert user.username == "mock-client"

        # Test authentication with invalid token
        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer invalid-token")],
        }
        conn = HTTPConnection(scope)

        result = await backend.authenticate(conn)  # type: ignore[attr-defined]
        assert result is None
