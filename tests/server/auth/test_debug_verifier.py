"""Unit tests for DebugTokenVerifier."""

import re

from fastmcp.server.auth.providers.debug import DebugTokenVerifier


class TestDebugTokenVerifier:
    """Test DebugTokenVerifier initialization and validation."""

    def test_init_defaults(self):
        """Test initialization with default parameters."""
        verifier = DebugTokenVerifier()

        assert verifier.client_id == "debug-client"
        assert verifier.scopes == []
        assert verifier.required_scopes == []
        assert callable(verifier.validate)

    def test_init_custom_parameters(self):
        """Test initialization with custom parameters."""
        verifier = DebugTokenVerifier(
            validate=lambda t: t.startswith("valid-"),
            client_id="custom-client",
            scopes=["read", "write"],
            required_scopes=["admin"],
        )

        assert verifier.client_id == "custom-client"
        assert verifier.scopes == ["read", "write"]
        assert verifier.required_scopes == ["admin"]

    async def test_verify_token_default_accepts_all(self):
        """Test that default verifier accepts all non-empty tokens."""
        verifier = DebugTokenVerifier()

        result = await verifier.verify_token("any-token")

        assert result is not None
        assert result.token == "any-token"
        assert result.client_id == "debug-client"
        assert result.scopes == []
        assert result.expires_at is None
        assert result.claims == {"token": "any-token"}

    async def test_verify_token_rejects_empty(self):
        """Test that empty tokens are rejected even with default verifier."""
        verifier = DebugTokenVerifier()

        # Empty string
        assert await verifier.verify_token("") is None

        # Whitespace only
        assert await verifier.verify_token("   ") is None

    async def test_verify_token_sync_callable_success(self):
        """Test token verification with custom sync callable that passes."""
        verifier = DebugTokenVerifier(
            validate=lambda t: t.startswith("valid-"),
            client_id="test-client",
            scopes=["read"],
        )

        result = await verifier.verify_token("valid-token-123")

        assert result is not None
        assert result.token == "valid-token-123"
        assert result.client_id == "test-client"
        assert result.scopes == ["read"]
        assert result.expires_at is None
        assert result.claims == {"token": "valid-token-123"}

    async def test_verify_token_sync_callable_failure(self):
        """Test token verification with custom sync callable that fails."""
        verifier = DebugTokenVerifier(validate=lambda t: t.startswith("valid-"))

        result = await verifier.verify_token("invalid-token")

        assert result is None

    async def test_verify_token_async_callable_success(self):
        """Test token verification with custom async callable that passes."""

        async def async_validator(token: str) -> bool:
            # Simulate async operation (e.g., database check)
            return token in {"token1", "token2", "token3"}

        verifier = DebugTokenVerifier(
            validate=async_validator,
            client_id="async-client",
            scopes=["admin"],
        )

        result = await verifier.verify_token("token2")

        assert result is not None
        assert result.token == "token2"
        assert result.client_id == "async-client"
        assert result.scopes == ["admin"]

    async def test_verify_token_async_callable_failure(self):
        """Test token verification with custom async callable that fails."""

        async def async_validator(token: str) -> bool:
            return token in {"token1", "token2", "token3"}

        verifier = DebugTokenVerifier(validate=async_validator)

        result = await verifier.verify_token("token99")

        assert result is None

    async def test_verify_token_callable_exception(self):
        """Test that exceptions in validate callable are handled gracefully."""

        def failing_validator(token: str) -> bool:
            raise ValueError("Something went wrong")

        verifier = DebugTokenVerifier(validate=failing_validator)

        result = await verifier.verify_token("any-token")

        assert result is None

    async def test_verify_token_async_callable_exception(self):
        """Test that exceptions in async validate callable are handled gracefully."""

        async def failing_async_validator(token: str) -> bool:
            raise ValueError("Async validation failed")

        verifier = DebugTokenVerifier(validate=failing_async_validator)

        result = await verifier.verify_token("any-token")

        assert result is None

    async def test_verify_token_whitelist_pattern(self):
        """Test using verifier with a whitelist of allowed tokens."""
        allowed_tokens = {"secret-token-1", "secret-token-2", "admin-token"}

        verifier = DebugTokenVerifier(validate=lambda t: t in allowed_tokens)

        # Allowed tokens
        assert await verifier.verify_token("secret-token-1") is not None
        assert await verifier.verify_token("admin-token") is not None

        # Disallowed tokens
        assert await verifier.verify_token("unknown-token") is None
        assert await verifier.verify_token("hacker-token") is None

    async def test_verify_token_pattern_matching(self):
        """Test using verifier with regex-like pattern matching."""

        pattern = re.compile(r"^[A-Z]{3}-\d{4}-[a-z]{2}$")

        verifier = DebugTokenVerifier(
            validate=lambda t: bool(pattern.match(t)),
            client_id="pattern-client",
        )

        # Valid patterns
        result = await verifier.verify_token("ABC-1234-xy")
        assert result is not None
        assert result.client_id == "pattern-client"

        # Invalid patterns
        assert await verifier.verify_token("abc-1234-xy") is None  # Wrong case
        assert await verifier.verify_token("ABC-123-xy") is None  # Wrong digits
        assert await verifier.verify_token("ABC-1234-xyz") is None  # Too many chars
