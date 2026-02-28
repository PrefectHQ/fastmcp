"""Tests for OAuth 2.0 Token Introspection verifier (RFC 7662)."""

import base64
import time
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from fastmcp.server.auth.providers.introspection import (
    IntrospectionTokenVerifier,
)


class TestIntrospectionTokenVerifier:
    """Test core token verification logic."""

    @pytest.fixture
    def verifier(self) -> IntrospectionTokenVerifier:
        """Create a basic introspection verifier for testing."""
        return IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            timeout_seconds=5,
        )

    @pytest.fixture
    def verifier_with_required_scopes(self) -> IntrospectionTokenVerifier:
        """Create verifier with required scopes."""
        return IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            required_scopes=["read", "write"],
        )

    def test_initialization(self):
        """Test verifier initialization."""
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
        )

        assert verifier.introspection_url == "https://auth.example.com/oauth/introspect"
        assert verifier.client_id == "test-client"
        assert verifier.client_secret == "test-secret"
        assert verifier.timeout_seconds == 10
        assert verifier.client_auth_method == "client_secret_basic"

    def test_initialization_requires_introspection_url(self):
        """Test that introspection_url is required."""
        with pytest.raises(TypeError):
            IntrospectionTokenVerifier(  # ty: ignore[missing-argument]
                client_id="test-client",
                client_secret="test-secret",
            )

    def test_initialization_requires_client_id(self):
        """Test that client_id is required."""
        with pytest.raises(TypeError):
            IntrospectionTokenVerifier(  # ty: ignore[missing-argument]
                introspection_url="https://auth.example.com/oauth/introspect",
                client_secret="test-secret",
            )

    def test_initialization_requires_client_secret(self):
        """Test that client_secret is required."""
        with pytest.raises(TypeError):
            IntrospectionTokenVerifier(  # ty: ignore[missing-argument]
                introspection_url="https://auth.example.com/oauth/introspect",
                client_id="test-client",
            )

    def test_create_basic_auth_header(self, verifier: IntrospectionTokenVerifier):
        """Test HTTP Basic Auth header creation."""
        auth_header = verifier._create_basic_auth_header()

        # Decode and verify
        assert auth_header.startswith("Basic ")
        encoded = auth_header[6:]
        decoded = base64.b64decode(encoded).decode("utf-8")
        assert decoded == "test-client:test-secret"

    def test_extract_scopes_from_string(self, verifier: IntrospectionTokenVerifier):
        """Test scope extraction from space-separated string."""
        response = {"scope": "read write admin"}
        scopes = verifier._extract_scopes(response)

        assert scopes == ["read", "write", "admin"]

    def test_extract_scopes_from_array(self, verifier: IntrospectionTokenVerifier):
        """Test scope extraction from array."""
        response = {"scope": ["read", "write", "admin"]}
        scopes = verifier._extract_scopes(response)

        assert scopes == ["read", "write", "admin"]

    def test_extract_scopes_missing(self, verifier: IntrospectionTokenVerifier):
        """Test scope extraction when scope field is missing."""
        response: dict[str, Any] = {}
        scopes = verifier._extract_scopes(response)

        assert scopes == []

    def test_extract_scopes_with_extra_whitespace(
        self, verifier: IntrospectionTokenVerifier
    ):
        """Test scope extraction handles extra whitespace."""
        response = {"scope": "  read   write  admin  "}
        scopes = verifier._extract_scopes(response)

        assert scopes == ["read", "write", "admin"]

    async def test_valid_token_verification(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test successful token verification."""
        # Mock introspection endpoint
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
                "scope": "read write",
                "exp": int(time.time()) + 3600,
                "iat": int(time.time()),
                "sub": "user-123",
                "username": "testuser",
            },
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is not None
        assert access_token.client_id == "user-123"
        assert access_token.scopes == ["read", "write"]
        assert access_token.expires_at is not None
        assert access_token.claims["active"] is True
        assert access_token.claims["username"] == "testuser"

    async def test_inactive_token_returns_none(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that inactive tokens return None."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": False},
        )

        access_token = await verifier.verify_token("expired-token")

        assert access_token is None

    async def test_expired_token_returns_none(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that expired tokens return None."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
                "scope": "read",
                "exp": int(time.time()) - 3600,  # Expired 1 hour ago
            },
        )

        access_token = await verifier.verify_token("expired-token")

        assert access_token is None

    async def test_token_without_expiration(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test token without expiration field."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
                "scope": "read",
            },
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is not None
        assert access_token.expires_at is None

    async def test_token_without_scopes(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test token without scope field."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
            },
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is not None
        assert access_token.scopes == []

    async def test_required_scopes_validation(
        self,
        verifier_with_required_scopes: IntrospectionTokenVerifier,
        httpx_mock: HTTPXMock,
    ):
        """Test that required scopes are validated."""
        # Token with insufficient scopes
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
                "scope": "read",  # Missing 'write'
            },
        )

        access_token = await verifier_with_required_scopes.verify_token("test-token")

        assert access_token is None

    async def test_required_scopes_validation_success(
        self,
        verifier_with_required_scopes: IntrospectionTokenVerifier,
        httpx_mock: HTTPXMock,
    ):
        """Test successful validation with required scopes."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
                "scope": "read write admin",  # Has all required scopes
            },
        )

        access_token = await verifier_with_required_scopes.verify_token("test-token")

        assert access_token is not None
        assert set(access_token.scopes) >= {"read", "write"}

    async def test_http_error_returns_none(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that HTTP errors return None."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            status_code=500,
            text="Internal Server Error",
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is None

    async def test_authentication_failure_returns_none(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that authentication failures return None."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            status_code=401,
            text="Unauthorized",
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is None

    async def test_timeout_returns_none(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that timeouts return None."""
        from httpx import TimeoutException

        httpx_mock.add_exception(
            TimeoutException("Request timed out"),
            url="https://auth.example.com/oauth/introspect",
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is None

    async def test_malformed_json_returns_none(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that malformed JSON responses return None."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            status_code=200,
            text="not json",
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is None

    async def test_request_includes_correct_headers(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that the request includes correct headers and auth."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )

        await verifier.verify_token("test-token")

        # Verify request was made with correct parameters
        request = httpx_mock.get_request()
        assert request is not None
        assert request.method == "POST"
        assert "Authorization" in request.headers
        assert request.headers["Authorization"].startswith("Basic ")
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert request.headers["Accept"] == "application/json"

    async def test_request_includes_token_and_hint(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that the request includes token and token_type_hint."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )

        await verifier.verify_token("my-test-token")

        request = httpx_mock.get_request()
        assert request is not None

        # Parse form data
        body = request.content.decode("utf-8")
        assert "token=my-test-token" in body
        assert "token_type_hint=access_token" in body

    async def test_client_id_fallback_to_sub(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that client_id falls back to sub if not present."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "sub": "user-456",
                "scope": "read",
            },
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is not None
        assert access_token.client_id == "user-456"

    async def test_client_id_defaults_to_unknown(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that client_id defaults to 'unknown' if neither client_id nor sub present."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "scope": "read",
            },
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is not None
        assert access_token.client_id == "unknown"

    def test_initialization_with_client_secret_post(self):
        """Test verifier initialization with client_secret_post method."""
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            client_auth_method="client_secret_post",
        )

        assert verifier.client_auth_method == "client_secret_post"
        assert verifier.introspection_url == "https://auth.example.com/oauth/introspect"
        assert verifier.client_id == "test-client"
        assert verifier.client_secret == "test-secret"

    def test_initialization_defaults_to_client_secret_basic(self):
        """Test that client_secret_basic is the default auth method."""
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
        )

        assert verifier.client_auth_method == "client_secret_basic"

    def test_initialization_rejects_invalid_client_auth_method(self):
        """Test that invalid client_auth_method values are rejected."""
        # Test typo with trailing space
        with pytest.raises(ValueError) as exc_info:
            IntrospectionTokenVerifier(
                introspection_url="https://auth.example.com/oauth/introspect",
                client_id="test-client",
                client_secret="test-secret",
                client_auth_method="client_secret_basic ",  # ty: ignore[invalid-argument-type]
            )
        assert "Invalid client_auth_method" in str(exc_info.value)
        assert "client_secret_basic " in str(exc_info.value)

        # Test completely invalid value
        with pytest.raises(ValueError) as exc_info:
            IntrospectionTokenVerifier(
                introspection_url="https://auth.example.com/oauth/introspect",
                client_id="test-client",
                client_secret="test-secret",
                client_auth_method="basic",  # ty: ignore[invalid-argument-type]
            )
        assert "Invalid client_auth_method" in str(exc_info.value)
        assert "basic" in str(exc_info.value)

    async def test_client_secret_post_includes_credentials_in_body(
        self, httpx_mock: HTTPXMock
    ):
        """Test that client_secret_post includes credentials in POST body."""
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            client_auth_method="client_secret_post",
        )

        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )

        await verifier.verify_token("test-token")

        # Verify request was made with credentials in body, not header
        request = httpx_mock.get_request()
        assert request is not None
        assert request.method == "POST"
        assert "Authorization" not in request.headers
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert request.headers["Accept"] == "application/json"

        # Parse form data
        body = request.content.decode("utf-8")
        assert "token=test-token" in body
        assert "token_type_hint=access_token" in body
        assert "client_id=test-client" in body
        assert "client_secret=test-secret" in body

    async def test_client_secret_post_verification_success(self, httpx_mock: HTTPXMock):
        """Test successful token verification with client_secret_post."""
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            client_auth_method="client_secret_post",
        )

        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
                "scope": "read write",
                "exp": int(time.time()) + 3600,
            },
        )

        access_token = await verifier.verify_token("test-token")

        assert access_token is not None
        assert access_token.client_id == "user-123"
        assert access_token.scopes == ["read", "write"]

    async def test_client_secret_basic_still_works(
        self, verifier: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that client_secret_basic continues to work unchanged."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )

        await verifier.verify_token("test-token")

        # Verify request was made with Basic Auth header
        request = httpx_mock.get_request()
        assert request is not None
        assert "Authorization" in request.headers
        assert request.headers["Authorization"].startswith("Basic ")

        # Verify credentials are NOT in body
        body = request.content.decode("utf-8")
        assert "client_id=" not in body
        assert "client_secret=" not in body


class TestIntrospectionCaching:
    """Test in-memory caching for token introspection."""

    @pytest.fixture
    def verifier_with_cache(self) -> IntrospectionTokenVerifier:
        """Create verifier with caching enabled."""
        return IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            cache_ttl_seconds=300,  # 5 minutes
            max_cache_size=100,
        )

    @pytest.fixture
    def verifier_no_cache(self) -> IntrospectionTokenVerifier:
        """Create verifier with caching disabled."""
        return IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            cache_ttl_seconds=0,  # Disabled
        )

    def test_default_cache_settings(self):
        """Test that caching is disabled by default."""
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
        )
        assert verifier._cache_ttl == 0  # Disabled by default
        assert verifier._max_cache_size == 10000

    def test_custom_cache_settings(self):
        """Test that cache settings can be customized."""
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            cache_ttl_seconds=60,
            max_cache_size=500,
        )
        assert verifier._cache_ttl == 60
        assert verifier._max_cache_size == 500

    def test_cache_disabled_with_zero_ttl(self):
        """Test that cache is disabled when TTL is 0 or None."""
        # Explicit 0
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            cache_ttl_seconds=0,
        )
        assert verifier._cache_ttl == 0

        # Explicit None (same as default)
        verifier2 = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            cache_ttl_seconds=None,
        )
        assert verifier2._cache_ttl == 0

    async def test_cache_disabled_with_zero_or_negative_max_size(
        self, httpx_mock: HTTPXMock
    ):
        """Test that cache is disabled when max_cache_size is 0 or negative."""
        # Add two responses for the two verifiers
        for _ in range(2):
            httpx_mock.add_response(
                url="https://auth.example.com/oauth/introspect",
                method="POST",
                json={
                    "active": True,
                    "client_id": "user-123",
                    "scope": "read",
                },
            )

        # Zero max_cache_size should disable caching (not raise StopIteration)
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            cache_ttl_seconds=300,
            max_cache_size=0,
        )
        result = await verifier.verify_token("test-token")
        assert result is not None
        assert result.client_id == "user-123"

        # Negative max_cache_size should also disable caching
        verifier2 = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            cache_ttl_seconds=300,
            max_cache_size=-1,
        )
        result2 = await verifier2.verify_token("test-token")
        assert result2 is not None

    async def test_cache_hit_returns_cached_result(
        self, verifier_with_cache: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that cached valid tokens are returned without introspection call."""
        # First call - introspection endpoint called
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
                "scope": "read write",
                "exp": int(time.time()) + 3600,
            },
        )

        # First verification
        result1 = await verifier_with_cache.verify_token("test-token")
        assert result1 is not None
        assert result1.client_id == "user-123"

        # Verify one request was made
        requests = httpx_mock.get_requests()
        assert len(requests) == 1

        # Second verification - should use cache, no new request
        result2 = await verifier_with_cache.verify_token("test-token")
        assert result2 is not None
        assert result2.client_id == "user-123"

        # Still only one request
        requests = httpx_mock.get_requests()
        assert len(requests) == 1

    async def test_cache_returns_defensive_copy(
        self, verifier_with_cache: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that cached tokens are defensive copies (mutations don't leak)."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
                "scope": "read write",
                "exp": int(time.time()) + 3600,
                "custom_claim": "original",
            },
        )

        # First verification
        result1 = await verifier_with_cache.verify_token("test-token")
        assert result1 is not None
        assert result1.claims["custom_claim"] == "original"

        # Mutate the result (simulating request-path code adding derived claims)
        result1.claims["custom_claim"] = "mutated"
        result1.claims["new_claim"] = "injected"
        result1.scopes.append("admin")

        # Second verification - should get clean copy, not mutated one
        result2 = await verifier_with_cache.verify_token("test-token")
        assert result2 is not None
        assert result2.claims["custom_claim"] == "original"
        assert "new_claim" not in result2.claims
        assert "admin" not in result2.scopes

        # Verify they are different object instances
        assert result1 is not result2

    async def test_inactive_tokens_not_cached(
        self, verifier_with_cache: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that inactive tokens are NOT cached (may become valid later)."""
        # Add two responses - inactive tokens should trigger re-introspection
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": False},
        )
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": False},
        )

        # First verification
        result1 = await verifier_with_cache.verify_token("inactive-token")
        assert result1 is None

        # Verify one request was made
        requests = httpx_mock.get_requests()
        assert len(requests) == 1

        # Second verification - should NOT use cache, makes another request
        result2 = await verifier_with_cache.verify_token("inactive-token")
        assert result2 is None

        # Two requests made (inactive tokens not cached)
        requests = httpx_mock.get_requests()
        assert len(requests) == 2

    async def test_cache_disabled_makes_every_call(
        self, verifier_no_cache: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that with caching disabled, every call makes a request."""
        # Add multiple responses for the same token
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )

        # First call
        await verifier_no_cache.verify_token("test-token")

        # Second call - should also make a request
        await verifier_no_cache.verify_token("test-token")

        # Two requests were made
        requests = httpx_mock.get_requests()
        assert len(requests) == 2

    async def test_different_tokens_are_cached_separately(
        self, verifier_with_cache: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that different tokens have separate cache entries."""
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-1"},
        )
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-2"},
        )

        # Verify two different tokens
        result1 = await verifier_with_cache.verify_token("token-1")
        result2 = await verifier_with_cache.verify_token("token-2")

        assert result1 is not None
        assert result1.client_id == "user-1"
        assert result2 is not None
        assert result2.client_id == "user-2"

        # Two requests were made
        requests = httpx_mock.get_requests()
        assert len(requests) == 2

        # Verify both again - no new requests
        await verifier_with_cache.verify_token("token-1")
        await verifier_with_cache.verify_token("token-2")

        requests = httpx_mock.get_requests()
        assert len(requests) == 2

    async def test_http_errors_are_not_cached(
        self, verifier_with_cache: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that HTTP errors are not cached (transient failures)."""
        # First call - HTTP error
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            status_code=500,
            text="Internal Server Error",
        )
        # Second call - success
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )

        # First verification - fails
        result1 = await verifier_with_cache.verify_token("test-token")
        assert result1 is None

        # Second verification - should retry since error wasn't cached
        result2 = await verifier_with_cache.verify_token("test-token")
        assert result2 is not None
        assert result2.client_id == "user-123"

        # Two requests were made
        requests = httpx_mock.get_requests()
        assert len(requests) == 2

    async def test_timeout_errors_are_not_cached(
        self, verifier_with_cache: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that timeout errors are not cached (transient failures)."""
        from httpx import TimeoutException

        # First call - timeout
        httpx_mock.add_exception(
            TimeoutException("Request timed out"),
            url="https://auth.example.com/oauth/introspect",
        )
        # Second call - success
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )

        # First verification - times out
        result1 = await verifier_with_cache.verify_token("test-token")
        assert result1 is None

        # Second verification - should retry since timeout wasn't cached
        result2 = await verifier_with_cache.verify_token("test-token")
        assert result2 is not None

        # Two requests were made
        requests = httpx_mock.get_requests()
        assert len(requests) == 2

    def test_token_hashing(self, verifier_with_cache: IntrospectionTokenVerifier):
        """Test that tokens are hashed consistently."""
        hash1 = verifier_with_cache._hash_token("test-token")
        hash2 = verifier_with_cache._hash_token("test-token")
        hash3 = verifier_with_cache._hash_token("different-token")

        # Same token produces same hash
        assert hash1 == hash2
        # Different tokens produce different hashes
        assert hash1 != hash3
        # Hash is a hex string (SHA-256 = 64 chars)
        assert len(hash1) == 64

    async def test_cache_respects_token_expiration(
        self, verifier_with_cache: IntrospectionTokenVerifier, httpx_mock: HTTPXMock
    ):
        """Test that cache respects token's exp claim for TTL."""
        # Token expiring in 60 seconds (shorter than cache TTL of 300)
        short_exp = int(time.time()) + 60

        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={
                "active": True,
                "client_id": "user-123",
                "exp": short_exp,
            },
        )

        await verifier_with_cache.verify_token("test-token")

        # Check that cache entry uses the shorter expiration
        cache_key = verifier_with_cache._hash_token("test-token")
        entry = verifier_with_cache._cache[cache_key]
        # Cache expiration should be at or before token expiration
        assert entry.expires_at <= short_exp

    async def test_expired_cache_entry_triggers_new_introspection(
        self, httpx_mock: HTTPXMock
    ):
        """Test that expired cache entries are evicted and a new call is made."""
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            cache_ttl_seconds=1,  # 1 second TTL
        )

        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )
        httpx_mock.add_response(
            url="https://auth.example.com/oauth/introspect",
            method="POST",
            json={"active": True, "client_id": "user-123"},
        )

        # First call — caches the result
        await verifier.verify_token("test-token")
        assert len(httpx_mock.get_requests()) == 1

        # Expire the cache entry manually
        cache_key = verifier._hash_token("test-token")
        verifier._cache[cache_key].expires_at = time.time() - 1

        # Second call — cache miss, new introspection
        await verifier.verify_token("test-token")
        assert len(httpx_mock.get_requests()) == 2

    async def test_cache_eviction_at_max_size(self, httpx_mock: HTTPXMock):
        """Test that cache evicts entries when max size is reached."""
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
            cache_ttl_seconds=300,
            max_cache_size=2,
        )

        for i in range(3):
            httpx_mock.add_response(
                url="https://auth.example.com/oauth/introspect",
                method="POST",
                json={"active": True, "client_id": f"user-{i}"},
            )

        # Fill cache to capacity
        await verifier.verify_token("token-0")
        await verifier.verify_token("token-1")
        assert len(verifier._cache) == 2

        # Third token should evict the oldest entry
        await verifier.verify_token("token-2")
        assert len(verifier._cache) == 2

        # token-0 should have been evicted (FIFO)
        hash_0 = verifier._hash_token("token-0")
        assert hash_0 not in verifier._cache


class TestIntrospectionTokenVerifierIntegration:
    """Integration tests with FastMCP server."""

    async def test_verifier_used_by_fastmcp(self):
        """Test that IntrospectionTokenVerifier can be used as FastMCP auth."""
        from fastmcp import FastMCP

        # Create verifier
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="test-client",
            client_secret="test-secret",
        )

        # Create protected server - should work without errors
        mcp = FastMCP("Test Server", auth=verifier)

        @mcp.tool()
        def greet(name: str) -> str:
            """Greet someone."""
            return f"Hello, {name}!"

        # Verify the auth is set correctly
        assert mcp.auth is verifier
        tools = await mcp.list_tools()
        assert len(list(tools)) == 1
