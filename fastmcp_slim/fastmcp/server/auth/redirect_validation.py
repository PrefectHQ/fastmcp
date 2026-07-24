"""Utilities for validating client redirect URIs in OAuth flows.

This module provides secure redirect URI validation with wildcard support,
protecting against userinfo-based bypass attacks like http://localhost@evil.com.
"""

import fnmatch
from urllib.parse import unquote, urlencode, urlparse, urlunparse

from pydantic import AnyUrl

UNSAFE_REDIRECT_URI_SCHEMES = frozenset(
    {
        "javascript",
        "data",
        "file",
        "vbscript",
    }
)


def add_query_params(url: str, params: dict[str, str]) -> str:
    """Append query parameters to a URL while preserving existing parameters.

    The existing query string is appended to verbatim rather than decoded
    and re-serialized, since registered redirect URIs may carry opaque or
    signed query strings whose exact bytes matter to the receiving client
    (for example, a valueless `?flag` must not become `?flag=`, and
    non-UTF-8 percent-encoded sequences must not be replaced).
    """
    parsed = urlparse(url)
    new_query = urlencode(params)
    query = f"{parsed.query}&{new_query}" if parsed.query else new_query
    return urlunparse(parsed._replace(query=query))


def replace_query_param(url: str, key: str, value: str) -> str:
    """Replace the first occurrence of `key` in a URL's query string in place.

    Like `add_query_params`, this does not round-trip the query through
    `parse_qsl`/`urlencode`: every segment other than the matched one is
    passed through byte-for-byte, so opaque or non-UTF-8 percent-encoded
    values elsewhere in the query are left untouched. Only the matched
    segment's encoding is replaced (with `key=value`, freshly
    `urlencode`d). If `key` is not present, it is appended, matching
    `add_query_params`'s behavior.
    """
    parsed = urlparse(url)
    segments = parsed.query.split("&") if parsed.query else []
    new_segment = urlencode({key: value})

    replaced = False
    new_segments: list[str] = []
    for segment in segments:
        segment_key = segment.split("=", 1)[0]
        if not replaced and unquote(segment_key) == key:
            new_segments.append(new_segment)
            replaced = True
        else:
            new_segments.append(segment)
    if not replaced:
        new_segments.append(new_segment)

    return urlunparse(parsed._replace(query="&".join(new_segments)))


def build_client_redirect(url: str, params: dict[str, str], *, iss: str) -> str:
    """Build a client-facing authorization redirect that carries exactly one `iss`.

    Every redirect this server sends back to an OAuth client from the
    authorization endpoint -- success (carrying `code`) or error (carrying
    `error`) -- must carry the proxy's RFC 9207 issuer exactly once (RFC
    6749 §3.1 forbids a response parameter from appearing more than once).
    A registered redirect_uri can legitimately carry its own `iss` query
    parameter already (e.g. `https://client.example/callback?iss=tenant`),
    so blindly appending the server's issuer on top of that would duplicate
    it -- this is what every client-facing redirect site must get right,
    and the reason this helper exists instead of five call sites each
    reimplementing the same invariant by hand.

    `params` is appended to `url` via `add_query_params` (verbatim, without
    re-encoding the existing query -- see that function's docstring), and
    `iss` is then set idempotently via `replace_query_param`: an existing
    occurrence -- whether contributed by the registered redirect_uri or
    already present in `url` -- is overwritten with the canonical value;
    otherwise `iss` is appended.

    `iss` is keyword-only and required so a caller cannot forget to pass
    it. `params` must not itself contain an `"iss"` key -- pass it via the
    `iss` keyword instead, so there is exactly one place the value can come
    from.

    Args:
        url: The redirect target -- normally the client's registered
            redirect_uri.
        params: The response parameters to append (e.g. `code`/`state`, or
            `error`/`error_description`). Must not include `"iss"`.
        iss: The canonical RFC 9207 issuer, byte-for-byte equal to the
            discovery document's `issuer` (`str(self.base_url)` /
            `self._issuer` -- never the rstripped `self._base_url`).

    Returns:
        `url` with `params` appended and exactly one `iss` query parameter
        set to `iss`.
    """
    if "iss" in params:
        raise ValueError(
            "params must not include 'iss' -- pass it via the 'iss' keyword"
        )
    if params:
        url = add_query_params(url, params)
    return replace_query_param(url, "iss", iss)


def _parse_host_port(netloc: str) -> tuple[str | None, str | None]:
    """Parse host and port from netloc, handling wildcards.

    Args:
        netloc: The netloc component (e.g., "localhost:8080" or "localhost:*")

    Returns:
        Tuple of (host, port_str) where port_str may be "*" or a number string
    """
    # Handle userinfo (remove it for parsing, but we check separately)
    if "@" in netloc:
        netloc = netloc.split("@")[-1]

    # Handle IPv6 addresses [::1]:port
    if netloc.startswith("["):
        bracket_end = netloc.find("]")
        if bracket_end == -1:
            return netloc, None
        host = netloc[1:bracket_end]
        rest = netloc[bracket_end + 1 :]
        if rest.startswith(":"):
            return host, rest[1:]
        return host, None

    # Handle regular host:port
    if ":" in netloc:
        host, port = netloc.rsplit(":", 1)
        return host, port

    return netloc, None


def _match_host(uri_host: str | None, pattern_host: str | None) -> bool:
    """Match host component, supporting *.example.com wildcard patterns.

    Args:
        uri_host: The host from the URI being validated
        pattern_host: The host pattern (may start with *.)

    Returns:
        True if the host matches
    """
    if not uri_host or not pattern_host:
        return uri_host == pattern_host

    # Normalize to lowercase for comparison
    uri_host = uri_host.lower()
    pattern_host = pattern_host.lower()

    # Handle *.example.com wildcard subdomain patterns
    if pattern_host.startswith("*."):
        suffix = pattern_host[1:]  # .example.com
        # Only match actual subdomains (foo.example.com), NOT the base domain
        return uri_host.endswith(suffix) and uri_host != pattern_host[2:]

    return uri_host == pattern_host


def _is_loopback_host(host: str | None) -> bool:
    """Check if a host is a loopback address.

    Per RFC 8252 §7.3, loopback addresses include localhost, 127.0.0.1, and ::1.
    """
    if not host:
        return False
    host = host.lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _match_port(
    uri_port: str | None,
    pattern_port: str | None,
    uri_scheme: str,
) -> bool:
    """Match port component, supporting * wildcard for any port.

    Args:
        uri_port: The port from the URI (None if default, string otherwise)
        pattern_port: The port from the pattern (None if default, "*" for wildcard)
        uri_scheme: The URI scheme (http/https) for default port handling

    Returns:
        True if the port matches
    """
    # Wildcard matches any port
    if pattern_port == "*":
        return True

    # Normalize None to default ports
    default_port = "443" if uri_scheme == "https" else "80"
    uri_effective = uri_port if uri_port else default_port
    pattern_effective = pattern_port if pattern_port else default_port

    return uri_effective == pattern_effective


def _has_dot_segments(path: str) -> bool:
    """Return True if a URI path contains `.` or `..` segments.

    Browsers collapse dot-segments when resolving a 302 Location per RFC
    3986 §5.2.4. Allowing them through the allowlist lets an attacker craft
    a URI that passes pattern matching but lands on a different path after
    redirect. Checks both the raw path and its percent-decoded form so that
    encoded variants like `/foo/%2e%2e/bar` are rejected.
    """
    for candidate in (path, unquote(path)):
        if any(seg in (".", "..") for seg in candidate.split("/")):
            return True
    return False


def _match_path(uri_path: str, pattern_path: str) -> bool:
    """Match path component using fnmatch for wildcard support.

    Args:
        uri_path: The path from the URI
        pattern_path: The path pattern (may contain * wildcards)

    Returns:
        True if the path matches
    """
    # Normalize empty paths to /
    uri_path = uri_path or "/"
    pattern_path = pattern_path or "/"

    # Empty or root pattern path matches any path
    # This makes http://localhost:* match http://localhost:3000/callback
    if pattern_path == "/":
        return True

    # Use fnmatch for path wildcards (e.g., /auth/*)
    return fnmatch.fnmatch(uri_path, pattern_path)


def _is_unsafe_redirect_uri(uri: str) -> bool:
    try:
        parsed = urlparse(uri)
    except ValueError:
        return True

    return parsed.scheme.lower() in UNSAFE_REDIRECT_URI_SCHEMES


def matches_allowed_pattern(uri: str, pattern: str) -> bool:
    """Securely check if a URI matches an allowed pattern with wildcard support.

    This function parses both the URI and pattern as URLs, comparing each
    component separately to prevent bypass attacks like userinfo injection.

    Patterns support wildcards:
    - http://localhost:* matches any localhost port
    - http://127.0.0.1:* matches any 127.0.0.1 port
    - https://*.example.com/* matches any subdomain of example.com
    - https://app.example.com/auth/* matches any path under /auth/

    Security: Rejects URIs with userinfo (user:pass@host) which could bypass
    naive string matching (e.g., http://localhost@evil.com).

    Args:
        uri: The redirect URI to validate
        pattern: The allowed pattern (may contain wildcards)

    Returns:
        True if the URI matches the pattern
    """
    try:
        uri_parsed = urlparse(uri)
        pattern_parsed = urlparse(pattern)
    except ValueError:
        return False

    if uri_parsed.scheme.lower() in UNSAFE_REDIRECT_URI_SCHEMES:
        return False

    # SECURITY: Reject URIs with userinfo (user:pass@host)
    # This prevents bypass attacks like http://localhost@evil.com/callback
    # which would match http://localhost:* with naive fnmatch
    if uri_parsed.username is not None or uri_parsed.password is not None:
        return False

    # SECURITY: Reject URIs with dot-segments in the path.
    # fnmatch's `*` matches across `/`, so a pattern like `/oauth/callback/*`
    # would accept `/oauth/callback/../../steal`; a browser receiving that in
    # a 302 Location resolves the dot-segments and lands at `/steal`, outside
    # the intended allowlist prefix. Reject at validation time so the stored
    # redirect_uri cannot later be emitted verbatim in a redirect.
    if _has_dot_segments(uri_parsed.path):
        return False

    # Scheme must match exactly
    if uri_parsed.scheme.lower() != pattern_parsed.scheme.lower():
        return False

    # Parse host and port manually to handle wildcards
    uri_host, uri_port = _parse_host_port(uri_parsed.netloc)
    pattern_host, pattern_port = _parse_host_port(pattern_parsed.netloc)

    # Host must match (with subdomain wildcard support)
    if not _match_host(uri_host, pattern_host):
        return False

    # RFC 8252 §7.3: loopback patterns without an explicit port match any port
    if not (_is_loopback_host(pattern_host) and pattern_port is None):
        if not _match_port(uri_port, pattern_port, uri_parsed.scheme.lower()):
            return False

    # Path must match (with fnmatch wildcards)
    return _match_path(uri_parsed.path, pattern_parsed.path)


def validate_redirect_uri(
    redirect_uri: str | AnyUrl | None,
    allowed_patterns: list[str] | None,
) -> bool:
    """Validate a redirect URI against allowed patterns.

    Args:
        redirect_uri: The redirect URI to validate
        allowed_patterns: List of allowed patterns. If None, ordinary URIs are allowed
                         for DCR compatibility, while unsafe browser schemes are rejected.
                         If empty list, no URIs are allowed.
                         To restrict to localhost only, explicitly pass DEFAULT_LOCALHOST_PATTERNS.

    Returns:
        True if the redirect URI is allowed
    """
    if redirect_uri is None:
        return True  # None is allowed (will use client's default)

    uri_str = str(redirect_uri)

    if _is_unsafe_redirect_uri(uri_str):
        return False

    # If no patterns specified, preserve broad DCR compatibility after the
    # unsafe browser-scheme check above.
    if allowed_patterns is None:
        return True

    # Check if URI matches any allowed pattern
    for pattern in allowed_patterns:
        if matches_allowed_pattern(uri_str, pattern):
            return True

    return False


# Default patterns for localhost-only validation
DEFAULT_LOCALHOST_PATTERNS = [
    "http://localhost:*",
    "http://127.0.0.1:*",
]
