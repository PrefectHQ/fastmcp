"""Utilities for validating client redirect URIs in OAuth flows."""

import fnmatch

from pydantic import AnyUrl


def matches_allowed_pattern(uri: str, pattern: str) -> bool:
    """Check if a URI matches an allowed pattern with wildcard support.

    Patterns support * wildcard matching:
    - http://localhost:* matches any localhost port
    - http://127.0.0.1:* matches any 127.0.0.1 port
    - https://*.example.com/* matches any subdomain of example.com
    - https://app.example.com/auth/* matches any path under /auth/

    Args:
        uri: The redirect URI to validate
        pattern: The allowed pattern (may contain wildcards)

    Returns:
        True if the URI matches the pattern
    """
    # Use fnmatch for wildcard matching
    return fnmatch.fnmatch(uri, pattern)


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
