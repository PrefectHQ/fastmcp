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
