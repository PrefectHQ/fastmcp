"""Utilities for validating client redirect URIs in OAuth flows.

This module provides secure redirect URI validation with wildcard support,
protecting against userinfo-based bypass attacks like http://localhost@evil.com.
"""

import fnmatch
from urllib.parse import urlparse

from pydantic import AnyUrl


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

    # SECURITY: Reject URIs with userinfo (user:pass@host)
    # This prevents bypass attacks like http://localhost@evil.com/callback
    # which would match http://localhost:* with naive fnmatch
    if uri_parsed.username is not None or uri_parsed.password is not None:
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

    # Port must match (with * wildcard support)
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
        allowed_patterns: List of allowed patterns. If None, all URIs are allowed (for DCR compatibility).
                         If empty list, no URIs are allowed.
                         To restrict to localhost only, explicitly pass DEFAULT_LOCALHOST_PATTERNS.

    Returns:
        True if the redirect URI is allowed
    """
    if redirect_uri is None:
        return True  # None is allowed (will use client's default)

    uri_str = str(redirect_uri)

    # If no patterns specified, allow all for DCR compatibility
    # (clients need to dynamically register with their own redirect URIs)
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
