"""Unit tests for JWT issuer and token encryption."""

import base64
import time

import pytest
from authlib.jose.errors import JoseError

from fastmcp.server.auth.jwt_issuer import (
    JWTIssuer,
    derive_jwt_key,
)


class TestKeyDerivation:
    """Tests for HKDF key derivation functions."""

    def test_derive_jwt_key_produces_32_bytes(self):
        """Test that JWT key derivation produces 32-byte key."""
        key = derive_jwt_key(high_entropy_material="test-secret", salt="test-salt")
        assert len(key) == 44
        assert isinstance(key, bytes)

        # base64 decode and make sure its 32 bytes
        key_bytes = base64.urlsafe_b64decode(key)
        assert len(key_bytes) == 32

        key = derive_jwt_key(low_entropy_material="test-secret", salt="test-salt")
        assert len(key) == 44
        assert isinstance(key, bytes)

        # base64 decode and make sure its 32 bytes
        key_bytes = base64.urlsafe_b64decode(key)
        assert len(key_bytes) == 32

    def test_derive_jwt_key_with_different_secrets_produces_different_keys(self):
        """Test that different secrets produce different keys."""
        key1 = derive_jwt_key(high_entropy_material="secret1", salt="salt")
        key2 = derive_jwt_key(high_entropy_material="secret2", salt="salt")
        assert key1 != key2

        key1 = derive_jwt_key(low_entropy_material="secret1", salt="salt")
        key2 = derive_jwt_key(low_entropy_material="secret2", salt="salt")
        assert key1 != key2

    def test_derive_jwt_key_with_different_salts_produces_different_keys(self):
        """Test that different salts produce different keys."""
        key1 = derive_jwt_key(high_entropy_material="secret", salt="salt1")
        key2 = derive_jwt_key(high_entropy_material="secret", salt="salt2")
        assert key1 != key2

        key1 = derive_jwt_key(low_entropy_material="secret", salt="salt1")
        key2 = derive_jwt_key(low_entropy_material="secret", salt="salt2")
        assert key1 != key2

    def test_derive_jwt_key_is_deterministic(self):
        """Test that same inputs always produce same key."""
        key1 = derive_jwt_key(high_entropy_material="secret", salt="salt")
        key2 = derive_jwt_key(high_entropy_material="secret", salt="salt")
        assert key1 == key2

        key1 = derive_jwt_key(low_entropy_material="secret", salt="salt")
        key2 = derive_jwt_key(low_entropy_material="secret", salt="salt")
        assert key1 == key2


class TestJWTIssuer:
    """Tests for JWT token issuance and verification."""

    @pytest.fixture
    def issuer(self):
        """Create a JWT issuer for testing."""
        signing_key = derive_jwt_key(
            low_entropy_material="test-secret", salt="test-salt"
        )
        return JWTIssuer(
            issuer="https://test-server.com",
            audience="https://test-server.com/mcp",
            signing_key=signing_key,
        )

    def test_issue_access_token_creates_valid_jwt(self, issuer):
        """Test that access token is a minimal JWT with correct structure."""
        token = issuer.issue_access_token(
            client_id="client-abc",
            scopes=["read", "write"],
            jti="token-id-123",
            expires_in=3600,
        )

        # Should be a JWT with 3 segments
        assert len(token.split(".")) == 3

        # Should be verifiable
        payload = issuer.verify_token(token)
        # Minimal token should only have required claims
        assert payload["client_id"] == "client-abc"
        assert payload["scope"] == "read write"
        assert payload["jti"] == "token-id-123"
        assert payload["iss"] == "https://test-server.com"
        assert payload["aud"] == "https://test-server.com/mcp"
        # Should NOT have user identity claims
        assert "sub" not in payload
        assert "azp" not in payload

    def test_minimal_token_has_no_user_identity(self, issuer):
        """Test that minimal tokens contain no user identity or custom claims."""
        token = issuer.issue_access_token(
            client_id="client-abc",
            scopes=["read"],
            jti="token-id",
            expires_in=3600,
        )

        payload = issuer.verify_token(token)
        # Should only have minimal required claims
        assert "sub" not in payload
        assert "azp" not in payload
        assert "groups" not in payload
        assert "roles" not in payload
        assert "email" not in payload
        # Should have exactly these claims
        expected_keys = {"iss", "aud", "client_id", "scope", "exp", "iat", "jti"}
        assert set(payload.keys()) == expected_keys

    def test_issue_refresh_token_creates_valid_jwt(self, issuer):
        """Test that refresh token is a minimal JWT with token_use claim."""
        token = issuer.issue_refresh_token(
            client_id="client-abc",
            scopes=["read"],
            jti="refresh-token-id",
            expires_in=60 * 60 * 24 * 30,  # 30 days
        )

        payload = issuer.verify_token(token)
        assert payload["client_id"] == "client-abc"
        assert payload["token_use"] == "refresh"
        assert payload["jti"] == "refresh-token-id"
        # Should NOT have user identity
        assert "sub" not in payload

    def test_verify_token_validates_signature(self, issuer):
        """Test that token verification fails with wrong signing key."""
        # Create token with one issuer
        token = issuer.issue_access_token(
            client_id="client-abc",
            scopes=["read"],
            jti="token-id",
        )

        # Try to verify with different issuer (different key)
        other_key = derive_jwt_key(
            low_entropy_material="different-secret", salt="different-salt"
        )
        other_issuer = JWTIssuer(
            issuer="https://test-server.com",
            audience="https://test-server.com/mcp",
            signing_key=other_key,
        )

        with pytest.raises(JoseError):
            other_issuer.verify_token(token)

    def test_verify_token_validates_expiration(self, issuer):
        """Test that expired tokens are rejected."""
        # Create token that expires in 1 second
        token = issuer.issue_access_token(
            client_id="client-abc",
            scopes=["read"],
            jti="token-id",
            expires_in=1,
        )

        # Should be valid immediately
        payload = issuer.verify_token(token)
        assert payload["client_id"] == "client-abc"

        # Wait for token to expire
        time.sleep(1.1)

        # Should be rejected
        with pytest.raises(JoseError, match="expired"):
            issuer.verify_token(token)

    def test_verify_token_validates_issuer(self, issuer):
        """Test that tokens from different issuers are rejected."""
        token = issuer.issue_access_token(
            client_id="client-abc",
            scopes=["read"],
            jti="token-id",
        )

        # Create issuer with different issuer URL but same key
        other_issuer = JWTIssuer(
            issuer="https://other-server.com",  # Different issuer
            audience="https://test-server.com/mcp",
            signing_key=issuer._signing_key,  # Same key
        )

        with pytest.raises(JoseError, match="issuer"):
            other_issuer.verify_token(token)

    def test_verify_token_validates_audience(self, issuer):
        """Test that tokens for different audiences are rejected."""
        token = issuer.issue_access_token(
            client_id="client-abc",
            scopes=["read"],
            jti="token-id",
        )

        # Create issuer with different audience but same key
        other_issuer = JWTIssuer(
            issuer="https://test-server.com",
            audience="https://other-server.com/mcp",  # Different audience
            signing_key=issuer._signing_key,  # Same key
        )

        with pytest.raises(JoseError, match="audience"):
            other_issuer.verify_token(token)

    def test_verify_token_rejects_malformed_tokens(self, issuer):
        """Test that malformed tokens are rejected."""
        with pytest.raises(JoseError):
            issuer.verify_token("not-a-jwt")

        with pytest.raises(JoseError):
            issuer.verify_token("too.few.segments")

        with pytest.raises(JoseError):
            issuer.verify_token("header.payload")  # Missing signature
