"""Tests for authentication utility helpers."""

import base64
import json

import pytest

from fastmcp.utilities.auth import (
    decode_jwt_header,
    decode_jwt_payload,
    parse_scopes,
)


def create_jwt(header: dict, payload: dict, signature: bytes = b"fake-sig") -> str:
    """Create a test JWT token."""
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")
    return f"{header_b64.decode()}.{payload_b64.decode()}.{sig_b64.decode()}"


class TestDecodeJwtHeader:
    """Tests for decode_jwt_header utility."""

    def test_decode_basic_header(self):
        """Test decoding a basic JWT header."""
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {"sub": "user-123"}
        token = create_jwt(header, payload)

        result = decode_jwt_header(token)

        assert result == header
        assert result["alg"] == "RS256"
        assert result["typ"] == "JWT"

    def test_decode_header_with_kid(self):
        """Test decoding header with key ID for JWKS lookup."""
        header = {"alg": "RS256", "typ": "JWT", "kid": "key-id-123"}
        payload = {"sub": "user-123"}
        token = create_jwt(header, payload)

        result = decode_jwt_header(token)

        assert result["kid"] == "key-id-123"

    def test_invalid_jwt_format_two_parts(self):
        """Test that two-part token raises ValueError."""
        with pytest.raises(ValueError, match="Invalid JWT format"):
            decode_jwt_header("header.payload")

    def test_invalid_jwt_format_one_part(self):
        """Test that single-part token raises ValueError."""
        with pytest.raises(ValueError, match="Invalid JWT format"):
            decode_jwt_header("not-a-jwt")

    def test_invalid_jwt_format_four_parts(self):
        """Test that four-part token raises ValueError."""
        with pytest.raises(ValueError, match="Invalid JWT format"):
            decode_jwt_header("a.b.c.d")

    def test_decode_header_length_divisible_by_4(self):
        """Test decoding when base64 length is divisible by 4 (no padding needed).

        This tests the edge case where len(part) % 4 == 0.
        The padding calculation (-len % 4) correctly yields 0 in this case.
        """
        # Create a header that encodes to exactly 12 chars (divisible by 4)
        header = {"x": ""}  # eyJ4IjogIiJ9 = 12 chars
        payload = {"sub": "user"}
        token = create_jwt(header, payload)

        result = decode_jwt_header(token)
        assert result == header


class TestDecodeJwtPayload:
    """Tests for decode_jwt_payload utility."""

    def test_decode_basic_payload(self):
        """Test decoding a basic JWT payload."""
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {"sub": "user-123", "name": "Test User"}
        token = create_jwt(header, payload)

        result = decode_jwt_payload(token)

        assert result == payload
        assert result["sub"] == "user-123"
        assert result["name"] == "Test User"

    def test_decode_payload_with_claims(self):
        """Test decoding payload with various claims."""
        header = {"alg": "RS256"}
        payload = {
            "sub": "user-123",
            "oid": "object-456",
            "name": "Test User",
            "email": "test@example.com",
            "roles": ["admin", "user"],
            "exp": 1234567890,
        }
        token = create_jwt(header, payload)

        result = decode_jwt_payload(token)

        assert result["sub"] == "user-123"
        assert result["oid"] == "object-456"
        assert result["name"] == "Test User"
        assert result["email"] == "test@example.com"
        assert result["roles"] == ["admin", "user"]
        assert result["exp"] == 1234567890

    def test_invalid_jwt_format(self):
        """Test that invalid format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid JWT format"):
            decode_jwt_payload("not-a-jwt")

    def test_decode_payload_with_padding_edge_cases(self):
        """Test that base64 padding is handled correctly."""
        # Create payloads of different sizes to test padding
        for payload_size in [1, 2, 3, 4, 5, 10, 100]:
            payload = {"data": "x" * payload_size}
            token = create_jwt({"alg": "RS256"}, payload)
            result = decode_jwt_payload(token)
            assert result == payload

    def test_decode_payload_length_divisible_by_4(self):
        """Test decoding when base64 length is divisible by 4 (no padding needed).

        This tests the edge case where len(part) % 4 == 0.
        The padding calculation (-len % 4) correctly yields 0 in this case.
        """
        # Create a payload that encodes to exactly 12 chars (divisible by 4)
        header = {"alg": "RS256"}
        payload = {"x": ""}  # eyJ4IjogIiJ9 = 12 chars
        token = create_jwt(header, payload)

        result = decode_jwt_payload(token)
        assert result == payload


class TestParseScopes:
    """Tests for parse_scopes utility."""

    def test_parse_none(self):
        """Test that None returns None."""
        assert parse_scopes(None) is None

    def test_parse_empty_string(self):
        """Test that empty string returns empty list."""
        assert parse_scopes("") == []

    def test_parse_space_separated(self):
        """Test parsing space-separated scopes."""
        assert parse_scopes("read write delete") == ["read", "write", "delete"]

    def test_parse_comma_separated(self):
        """Test parsing comma-separated scopes."""
        assert parse_scopes("read,write,delete") == ["read", "write", "delete"]

    def test_parse_json_array(self):
        """Test parsing JSON array string."""
        assert parse_scopes('["read", "write", "delete"]') == [
            "read",
            "write",
            "delete",
        ]

    def test_parse_list(self):
        """Test that list is returned as-is."""
        assert parse_scopes(["read", "write"]) == ["read", "write"]

    def test_parse_strips_whitespace(self):
        """Test that whitespace is stripped."""
        assert parse_scopes("  read  ,  write  ") == ["read", "write"]
