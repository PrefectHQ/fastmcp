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


class TestSecurityBypass:
    """Test protection against redirect URI security bypass attacks."""

    def test_userinfo_bypass_blocked(self):
        """Test that userinfo-style bypasses are blocked.

        Attack: http://localhost@evil.com/callback would match http://localhost:*
        with naive string matching, but actually points to evil.com.
        """
        pattern = "http://localhost:*"

        # These should be blocked - the "host" is actually in the userinfo
        assert not matches_allowed_pattern(
            "http://localhost@evil.com/callback", pattern
        )
        assert not matches_allowed_pattern(
            "http://localhost:3000@malicious.io/callback", pattern
        )
        assert not matches_allowed_pattern(
            "http://user:pass@localhost:3000/callback", pattern
        )

    def test_userinfo_bypass_with_subdomain_pattern(self):
        """Test userinfo bypass with subdomain wildcard patterns."""
        pattern = "https://*.example.com/callback"

        # Blocked: userinfo tricks
        assert not matches_allowed_pattern(
            "https://app.example.com@attacker.com/callback", pattern
        )
        assert not matches_allowed_pattern(
            "https://user:pass@app.example.com/callback", pattern
        )

    def test_legitimate_uris_still_work(self):
        """Test that legitimate URIs work after security hardening."""
        pattern = "http://localhost:*"
        assert matches_allowed_pattern("http://localhost:3000/callback", pattern)
        assert matches_allowed_pattern("http://localhost:8080/auth", pattern)

        pattern = "https://*.example.com/callback"
        assert matches_allowed_pattern("https://app.example.com/callback", pattern)

    def test_scheme_mismatch_blocked(self):
        """Test that scheme mismatches are blocked."""
        assert not matches_allowed_pattern(
            "http://localhost:3000/callback", "https://localhost:*"
        )
        assert not matches_allowed_pattern(
            "https://localhost:3000/callback", "http://localhost:*"
        )

    def test_host_mismatch_blocked(self):
        """Test that host mismatches are blocked even with wildcards."""
        pattern = "http://localhost:*"
        assert not matches_allowed_pattern("http://127.0.0.1:3000/callback", pattern)
        assert not matches_allowed_pattern("http://example.com:3000/callback", pattern)


class TestDotSegmentBypass:
    """Tests for GHSA-8gcw-8ch3-fh9v: dot-segment bypass of path allowlists.

    A pattern like `/oauth/callback/*` matches `/oauth/callback/../../steal`
    via `fnmatch`, because `*` matches across `/`. Browsers resolve the
    dot-segments on redirect, landing at a path outside the allowlist prefix.
    The fix rejects dot-segments (raw and percent-encoded) during validation.
    """

    def test_traversal_segments_rejected(self):
        pattern = "https://app.example.com/oauth/callback/*"
        assert not matches_allowed_pattern(
            "https://app.example.com/oauth/callback/../../steal", pattern
        )

    def test_percent_encoded_traversal_rejected(self):
        pattern = "https://app.example.com/oauth/callback/*"
        for encoded in ("%2e%2e", "%2E%2E", "%2e%2E"):
            assert not matches_allowed_pattern(
                f"https://app.example.com/oauth/callback/{encoded}/{encoded}/steal",
                pattern,
            )

    def test_single_dot_segment_rejected(self):
        pattern = "https://app.example.com/oauth/callback/*"
        assert not matches_allowed_pattern(
            "https://app.example.com/oauth/callback/./foo", pattern
        )

    def test_trailing_dotdot_rejected(self):
        pattern = "https://app.example.com/oauth/callback/*"
        assert not matches_allowed_pattern(
            "https://app.example.com/oauth/callback/foo/..", pattern
        )

    def test_percent_encoded_single_dot_rejected(self):
        pattern = "https://app.example.com/oauth/callback/*"
        assert not matches_allowed_pattern(
            "https://app.example.com/oauth/callback/%2e/foo", pattern
        )

    def test_mixed_raw_and_encoded_rejected(self):
        pattern = "https://app.example.com/oauth/callback/*"
        assert not matches_allowed_pattern(
            "https://app.example.com/oauth/callback/..%2fsteal", pattern
        )

    def test_legitimate_nested_paths_still_match(self):
        """Paths that happen to contain dots in segment names must still match."""
        pattern = "https://app.example.com/oauth/callback/*"
        for uri in (
            "https://app.example.com/oauth/callback/foo",
            "https://app.example.com/oauth/callback/deeply/nested/path",
            "https://app.example.com/oauth/callback/file.ext",
            "https://app.example.com/oauth/callback/v1.2.3/item",
        ):
            assert matches_allowed_pattern(uri, pattern), uri


class TestLoopbackPortMatching:
    """Test RFC 8252 §7.3: loopback URIs with no port in pattern match any port."""

    def test_localhost_no_port_matches_any_port(self):
        """Pattern http://localhost/callback should match any port on localhost."""
        pattern = "http://localhost/callback"
        assert matches_allowed_pattern("http://localhost:51353/callback", pattern)
        assert matches_allowed_pattern("http://localhost:3000/callback", pattern)
        assert matches_allowed_pattern("http://localhost:80/callback", pattern)

    def test_localhost_no_port_no_path_matches_any_port(self):
        """Pattern http://localhost should match any port on localhost."""
        pattern = "http://localhost"
        assert matches_allowed_pattern("http://localhost:51353", pattern)
        assert matches_allowed_pattern("http://localhost:3000/callback", pattern)

    def test_127_0_0_1_no_port_matches_any_port(self):
        """Pattern http://127.0.0.1/callback should match any port on 127.0.0.1."""
        pattern = "http://127.0.0.1/callback"
        assert matches_allowed_pattern("http://127.0.0.1:51353/callback", pattern)
        assert matches_allowed_pattern("http://127.0.0.1:3000/callback", pattern)

    def test_ipv6_loopback_no_port_matches_any_port(self):
        """Pattern http://[::1]/callback should match any port on [::1]."""
        pattern = "http://[::1]/callback"
        assert matches_allowed_pattern("http://[::1]:51353/callback", pattern)
        assert matches_allowed_pattern("http://[::1]:3000/callback", pattern)

    def test_non_loopback_no_port_requires_default_port(self):
        """Non-loopback patterns without port should still require default port."""
        pattern = "http://example.com/callback"
        # Should only match port 80 (default for HTTP)
        assert matches_allowed_pattern("http://example.com/callback", pattern)
        assert matches_allowed_pattern("http://example.com:80/callback", pattern)
        assert not matches_allowed_pattern("http://example.com:3000/callback", pattern)

    def test_loopback_explicit_port_requires_exact_match(self):
        """Loopback patterns with an explicit port should still require exact match."""
        pattern = "http://localhost:8080/callback"
        assert matches_allowed_pattern("http://localhost:8080/callback", pattern)
        assert not matches_allowed_pattern("http://localhost:3000/callback", pattern)

    def test_loopback_no_port_still_checks_scheme(self):
        """Scheme must still match even for loopback URIs."""
        pattern = "http://localhost/callback"
        assert not matches_allowed_pattern("https://localhost:3000/callback", pattern)

    def test_loopback_no_port_still_checks_host(self):
        """Host must still match even for loopback URIs."""
        pattern = "http://localhost/callback"
        assert not matches_allowed_pattern("http://example.com:3000/callback", pattern)

    def test_loopback_no_port_still_checks_path(self):
        """Path must still match even for loopback URIs."""
        pattern = "http://localhost/callback"
        assert not matches_allowed_pattern("http://localhost:3000/other", pattern)


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
