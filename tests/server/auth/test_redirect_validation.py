"""Tests for redirect URI validation in OAuth flows."""

import re
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlparse

import pytest
from pydantic import AnyUrl

import fastmcp.server.auth
from fastmcp.server.auth.redirect_validation import (
    DEFAULT_LOCALHOST_PATTERNS,
    add_query_params,
    build_client_redirect,
    matches_allowed_pattern,
    replace_query_param,
    validate_redirect_uri,
)


class TestAddQueryParams:
    """Test that add_query_params preserves the registered callback's exact query bytes.

    A registered redirect URI may carry an opaque or signed query string.
    Decoding it with parse_qsl and re-serializing with urlencode mutates it
    (a valueless `?flag` becomes `?flag=`, and non-UTF-8 percent-encoded
    bytes get replaced) which breaks clients that route on, or
    cryptographically validate, the raw callback query.
    """

    def test_preserves_valueless_param_and_non_utf8_bytes(self):
        original_query = "flag&sig=%FF%FE"
        url = f"https://client.example.com/callback?{original_query}"

        result = add_query_params(
            url,
            {
                "code": "abc123",
                "state": "xyz state",
                "iss": "https://issuer.example.com/",
            },
        )

        result_query = urlparse(result).query

        # The original query substring must survive byte-for-byte: the
        # valueless `flag` must not become `flag=`, and the non-UTF-8
        # percent-encoded `sig` value must not be decoded/replaced.
        assert result_query.startswith(f"{original_query}&")

        # New params are appended after a single `&`, correctly encoded.
        appended = result_query[len(original_query) + 1 :]
        assert dict(parse_qsl(appended)) == {
            "code": "abc123",
            "state": "xyz state",
            "iss": "https://issuer.example.com/",
        }

    def test_empty_query_has_no_stray_ampersand(self):
        url = "https://client.example.com/callback"

        result = add_query_params(url, {"code": "abc123"})

        assert result == "https://client.example.com/callback?code=abc123"

    def test_appends_to_existing_ordinary_query(self):
        url = "https://client.example.com/callback?foo=bar"

        result = add_query_params(url, {"code": "abc123"})

        assert result == "https://client.example.com/callback?foo=bar&code=abc123"


class TestReplaceQueryParam:
    """Direct tests for the idempotent replace-or-append primitive that
    `build_client_redirect` relies on to guarantee exactly one `iss`.
    """

    def test_replaces_existing_value_in_place_preserving_other_bytes(self):
        url = "https://client.example.com/callback?iss=tenant&sig=%FF%FE"

        result = replace_query_param(url, "iss", "https://issuer.example.com/")

        assert urlparse(result).query == (
            "iss=https%3A%2F%2Fissuer.example.com%2F&sig=%FF%FE"
        )

    def test_appends_when_key_absent(self):
        url = "https://client.example.com/callback?sig=%FF%FE"

        result = replace_query_param(url, "iss", "https://issuer.example.com/")

        assert urlparse(result).query == (
            "sig=%FF%FE&iss=https%3A%2F%2Fissuer.example.com%2F"
        )

    def test_only_first_occurrence_is_replaced(self):
        """A key appearing twice in the input is left with one replaced
        occurrence and one untouched -- callers must not feed this function
        an already-duplicated key and expect deduplication."""
        url = "https://client.example.com/callback?iss=first&iss=second"

        result = replace_query_param(url, "iss", "https://issuer.example.com/")

        assert urlparse(result).query == (
            "iss=https%3A%2F%2Fissuer.example.com%2F&iss=second"
        )


class TestBuildClientRedirect:
    """Tests for the single helper that owns the client-facing-redirect
    `iss` invariant: exactly one `iss`, set to the canonical value, with
    every other query byte preserved verbatim.

    This is the consolidation point for RFC 9207 support -- every redirect
    the OAuth proxy sends back to a client (success or error, across all
    five call sites that build one) must go through this function rather
    than hand-building a params dict with its own `"iss"` key.
    """

    def test_appends_params_and_iss_when_absent(self):
        url = "https://client.example.com/callback"

        result = build_client_redirect(
            url,
            {"code": "abc", "state": "xyz"},
            iss="https://issuer.example.com/",
        )

        assert dict(parse_qsl(urlparse(result).query)) == {
            "code": "abc",
            "state": "xyz",
            "iss": "https://issuer.example.com/",
        }

    def test_replaces_iss_already_present_in_registered_redirect_uri(self):
        """A registered redirect_uri may legitimately carry its own `iss`
        query parameter (e.g. a multi-tenant client encoding its tenant in
        the callback URL). Blindly appending the server's issuer on top of
        that would yield two `iss` values -- RFC 6749 §3.1 forbids a
        response parameter appearing more than once, so strict clients
        reject the response or read the wrong value. This is the P2 defect
        this helper exists to close off at every call site, not just one.
        """
        url = "https://client.example.com/callback?iss=tenant&sig=%FF%FE"

        result = build_client_redirect(
            url,
            {"code": "abc", "state": "xyz"},
            iss="https://issuer.example.com/",
        )

        result_query = urlparse(result).query
        iss_values = parse_qs(result_query)["iss"]
        assert len(iss_values) == 1
        assert iss_values == ["https://issuer.example.com/"]

    def test_preserves_valueless_param_and_non_utf8_bytes_alongside_existing_iss(
        self,
    ):
        """Exact end-to-end reproduction of the worked example from the P2
        review comment: registered redirect_uri already has `iss`, a
        valueless `flag`, and a non-UTF-8 percent-encoded `sig` -- all three
        must survive the round trip through `add_query_params` +
        `replace_query_param` untouched, with only `iss` rewritten in
        place.
        """
        url = "https://client.example.com/callback?iss=tenant&flag&sig=%FF%FE"

        result = build_client_redirect(
            url,
            {"code": "abc", "state": "xyz state"},
            iss="https://issuer.example.com/",
        )

        assert urlparse(result).query == (
            "iss=https%3A%2F%2Fissuer.example.com%2F"
            "&flag&sig=%FF%FE&code=abc&state=xyz+state"
        )

    def test_rejects_iss_hand_specified_in_params(self):
        """`iss` must come from the keyword-only `iss` argument, never from
        the `params` dict -- this keeps exactly one place a caller can set
        it, rather than two that could disagree."""
        with pytest.raises(ValueError, match="iss"):
            build_client_redirect(
                "https://client.example.com/callback",
                {"code": "abc", "iss": "sneaky"},
                iss="https://issuer.example.com/",
            )

    def test_empty_params_does_not_add_stray_ampersand(self):
        """The authorize-handler call site passes no extra params (it only
        needs to fix up `iss` on a URL the SDK already built) -- an empty
        `params` dict must not introduce a trailing/stray `&`."""
        url = "https://client.example.com/callback?code=abc&state=xyz"

        result = build_client_redirect(url, {}, iss="https://issuer.example.com/")

        assert urlparse(result).query == (
            "code=abc&state=xyz&iss=https%3A%2F%2Fissuer.example.com%2F"
        )
        assert "&&" not in result
        assert not result.endswith("&")


class TestNoHandSpecifiedIssOutsideHelper:
    """Guard against a future call site reintroducing the duplicate-`iss`
    bug this PR consolidates away.

    This is the sixth review round on the RFC 9207 `iss` work, and the last
    two rounds were the same defect surfacing at different call sites: a
    caller hand-building a params dict with its own `"iss"` key instead of
    routing through `build_client_redirect`. Rather than trust that every
    future redirect site remembers to do this, scan the directories that
    build client-facing authorization redirects (`oauth_proxy/`,
    `handlers/`) for a dict-literal `"iss"` key. `jwt_issuer.py` and the
    JWT/Clerk providers legitimately use `"iss"` as a JWT claim name, but
    those live outside these two directories, so this scan does not need to
    special-case them.
    """

    def test_no_dict_literal_iss_key_in_redirect_building_modules(self):
        auth_root = Path(fastmcp.server.auth.__file__).parent
        scan_dirs = [auth_root / "oauth_proxy", auth_root / "handlers"]
        iss_dict_key = re.compile(r"""["']iss["']\s*:""")

        offenders = [
            str(path)
            for scan_dir in scan_dirs
            for path in scan_dir.rglob("*.py")
            if iss_dict_key.search(path.read_text())
        ]

        assert not offenders, (
            "Found a hand-specified 'iss' dict key outside "
            "build_client_redirect() in: "
            f"{offenders}. Route this redirect through "
            "fastmcp.server.auth.redirect_validation.build_client_redirect "
            "instead so the duplicate-iss invariant stays centralized."
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

    @pytest.mark.parametrize(
        "uri",
        [
            "http://localhost:3000",
            "http://127.0.0.1:8080",
            "http://example.com",
            "https://app.example.com",
            "https://claude.ai/api/mcp/auth_callback",
            "cursor://anysphere.cursor-mcp/oauth/callback",
        ],
    )
    def test_default_allows_dcr_compatible_redirects(self, uri: str):
        """None preserves broad DCR compatibility for ordinary redirect URIs."""
        assert validate_redirect_uri(uri, None)

    @pytest.mark.parametrize(
        "uri",
        [
            "javascript:alert(document.cookie)//",
            "JaVaScRiPt:alert(document.cookie)//",
            "data:text/html,<script>alert(1)</script>",
            "file:///tmp/callback",
            "vbscript:msgbox(1)",
        ],
    )
    def test_default_rejects_unsafe_browser_schemes(self, uri: str):
        """Default DCR compatibility must not allow active browser schemes."""
        assert not validate_redirect_uri(uri, None)

    @pytest.mark.parametrize(
        "uri,pattern",
        [
            ("javascript:alert(document.cookie)//", "javascript:*"),
            ("data:text/html,<script>alert(1)</script>", "data:*"),
            ("file:///tmp/callback", "file:///*"),
            ("vbscript:msgbox(1)", "vbscript:*"),
        ],
    )
    def test_custom_patterns_cannot_allow_unsafe_browser_schemes(
        self, uri: str, pattern: str
    ):
        """Unsafe browser schemes stay blocked even if a pattern names them."""
        assert not validate_redirect_uri(uri, [pattern])

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
    """Tests for dot-segment bypass of path allowlists.

    A pattern like `/oauth/callback/*` would otherwise match
    `/oauth/callback/../../steal` via `fnmatch`, because `*` matches across
    `/`. Browsers resolve the dot-segments on redirect, landing at a path
    outside the allowlist prefix. The validator rejects dot-segments (raw
    and percent-encoded) up front.
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
