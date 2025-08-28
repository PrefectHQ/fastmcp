"""Tests for redirect URI validation in OAuth flows."""

from pydantic import AnyUrl

from fastmcp.server.auth.redirect_validation import (
    DEFAULT_LOCALHOST_PATTERNS,
    matches_allowed_pattern,
    validate_redirect_uri,
)


class TestMatchesAllowedPattern:
    """Test wildcard pattern matching for redirect URIs."""

    def test_exact_match(self):
        """Test exact URI matching without wildcards."""
        assert matches_allowed_pattern(
            "http://localhost:3000/callback", "http://localhost:3000/callback"
        )
        assert not matches_allowed_pattern(
            "http://localhost:3000/callback", "http://localhost:3001/callback"
        )

    def test_port_wildcard(self):
        """Test wildcard matching for ports."""
        pattern = "http://localhost:*/callback"
        assert matches_allowed_pattern("http://localhost:3000/callback", pattern)
        assert matches_allowed_pattern("http://localhost:54321/callback", pattern)
        assert not matches_allowed_pattern("http://example.com:3000/callback", pattern)

    def test_path_wildcard(self):
        """Test wildcard matching for paths."""
        pattern = "http://localhost:3000/*"
        assert matches_allowed_pattern("http://localhost:3000/callback", pattern)
        assert matches_allowed_pattern("http://localhost:3000/auth/callback", pattern)
        assert not matches_allowed_pattern("http://localhost:3001/callback", pattern)

    def test_subdomain_wildcard(self):
        """Test wildcard matching for subdomains."""
        pattern = "https://*.example.com/callback"
        assert matches_allowed_pattern("https://app.example.com/callback", pattern)
        assert matches_allowed_pattern("https://api.example.com/callback", pattern)
        assert not matches_allowed_pattern("https://example.com/callback", pattern)
        assert not matches_allowed_pattern("http://app.example.com/callback", pattern)

    def test_multiple_wildcards(self):
        """Test patterns with multiple wildcards."""
        pattern = "https://*.example.com:*/auth/*"
        assert matches_allowed_pattern(
            "https://app.example.com:8080/auth/callback", pattern
        )
        assert matches_allowed_pattern(
            "https://api.example.com:3000/auth/redirect", pattern
        )
        assert not matches_allowed_pattern(
            "http://app.example.com:8080/auth/callback", pattern
        )


class TestValidateRedirectUri:
    """Test redirect URI validation with pattern lists."""

    def test_none_redirect_uri_allowed(self):
        """Test that None redirect URI is always allowed."""
        assert validate_redirect_uri(None, None)
        assert validate_redirect_uri(None, [])
        assert validate_redirect_uri(None, ["http://localhost:*"])

    def test_default_allows_all(self):
        """Test that None (default) allows all URIs for DCR compatibility."""
        # All URIs should be allowed when None is provided (DCR compatibility)
        assert validate_redirect_uri("http://localhost:3000", None)
        assert validate_redirect_uri("http://127.0.0.1:8080", None)
        assert validate_redirect_uri("http://example.com", None)
        assert validate_redirect_uri("https://app.example.com", None)
        assert validate_redirect_uri("https://claude.ai/api/mcp/auth_callback", None)

    def test_empty_list_allows_none(self):
        """Test that empty list allows no redirect URIs."""
        assert not validate_redirect_uri("http://localhost:3000", [])
        assert not validate_redirect_uri("http://example.com", [])
        assert not validate_redirect_uri("https://anywhere.com:9999/path", [])

    def test_custom_patterns(self):
        """Test validation with custom pattern list."""
        patterns = [
            "http://localhost:*",
            "https://app.example.com/*",
            "https://*.trusted.io/*",
        ]

        # Allowed URIs
        assert validate_redirect_uri("http://localhost:3000", patterns)
        assert validate_redirect_uri("https://app.example.com/callback", patterns)
        assert validate_redirect_uri("https://api.trusted.io/auth", patterns)

        # Rejected URIs
        assert not validate_redirect_uri("http://127.0.0.1:3000", patterns)
        assert not validate_redirect_uri("https://other.example.com/callback", patterns)
        assert not validate_redirect_uri("http://app.example.com/callback", patterns)

    def test_anyurl_conversion(self):
        """Test that AnyUrl objects are properly converted to strings."""
        patterns = ["http://localhost:*"]
        uri = AnyUrl("http://localhost:3000/callback")
        assert validate_redirect_uri(uri, patterns)

        uri = AnyUrl("http://example.com/callback")
        assert not validate_redirect_uri(uri, patterns)


class TestDefaultPatterns:
    """Test the default localhost patterns constant."""

    def test_default_patterns_exist(self):
        """Test that default patterns are defined."""
        assert DEFAULT_LOCALHOST_PATTERNS is not None
        assert len(DEFAULT_LOCALHOST_PATTERNS) > 0

    def test_default_patterns_include_localhost(self):
        """Test that default patterns include localhost variations."""
        assert "http://localhost:*" in DEFAULT_LOCALHOST_PATTERNS
        assert "http://127.0.0.1:*" in DEFAULT_LOCALHOST_PATTERNS

    def test_explicit_localhost_patterns(self):
        """Test that explicitly passing DEFAULT_LOCALHOST_PATTERNS restricts to localhost."""
        # Localhost patterns should be allowed
        assert validate_redirect_uri(
            "http://localhost:3000", DEFAULT_LOCALHOST_PATTERNS
        )
        assert validate_redirect_uri(
            "http://127.0.0.1:8080", DEFAULT_LOCALHOST_PATTERNS
        )

        # Non-localhost should be rejected
        assert not validate_redirect_uri(
            "http://example.com", DEFAULT_LOCALHOST_PATTERNS
        )
        assert not validate_redirect_uri(
            "https://claude.ai/api/mcp/auth_callback", DEFAULT_LOCALHOST_PATTERNS
        )
