"""Utilities for validating client redirect URIs in OAuth flows.

This module provides secure redirect URI validation with wildcard support,
protecting against userinfo-based bypass attacks like http://localhost@evil.com.
"""

import fnmatch
import ipaddress
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


def is_loopback_host(host: str | None) -> bool:
    """Check if a host is a loopback address.

    Per RFC 8252 §7.3, loopback covers the whole reserved loopback range, not
    just the two familiar literals: IPv4 `127.0.0.0/8` (so `127.0.0.2` and
    `127.5.5.5` are loopback just as much as `127.0.0.1`) and IPv6 `::1`. IP
    hosts are therefore classified with `ipaddress.ip_address().is_loopback`
    rather than string equality — checking only `127.0.0.1` would let a web
    client register `https://127.0.0.2/callback` and slip past the
    non-loopback requirement.

    Names are handled per RFC 6761 §6.3, which reserves the entire `localhost`
    namespace for the local machine: the exact name `localhost` *and* any
    subdomain of it (`app.localhost`, `api.app.localhost`). `.localhost` is a
    reserved TLD that cannot be registered, so a subdomain of it always resolves
    to the loopback interface and must count as loopback in both directions —
    otherwise a web client could register `https://app.localhost/callback` and
    slip past the non-loopback requirement, while a native client using
    `http://app.localhost:3000/callback` would be wrongly rejected.

    The suffix test is anchored on a leading dot so it cannot be spoofed by a
    registrable domain: `localhost.evil.com` and `notlocalhost` are ordinary
    public names and are *not* loopback.

    Hosts are also normalized before classification: bracketed IPv6 literals
    (`[::1]`) are unwrapped, and a single trailing dot (the absolute/FQDN form,
    e.g. `localhost.` or `127.0.0.1.`) is stripped, since it denotes the same
    host. Non-IP hosts fall through to the name check without raising.
    """
    if not host:
        return False

    host = host.lower()

    # urlparse().hostname strips brackets, but callers that parse the netloc
    # themselves may still pass a bracketed IPv6 literal.
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    # Absolute (fully qualified) form: `localhost.` and `127.0.0.1.` name the
    # same hosts as their relative spellings.
    if host.endswith("."):
        host = host[:-1]

    if not host:
        return False

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not an IP literal — RFC 6761 §6.3 reserved localhost namespace.
        return host == "localhost" or host.endswith(".localhost")


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
    if not (is_loopback_host(pattern_host) and pattern_port is None):
        if not _match_port(uri_port, pattern_port, uri_parsed.scheme.lower()):
            return False

    # Path must match (with fnmatch wildcards)
    return _match_path(uri_parsed.path, pattern_parsed.path)


def is_redirect_uri_allowed_for_application_type(
    redirect_uri: str | AnyUrl,
    application_type: str | None,
) -> bool:
    """Check a redirect URI against RFC 7591 / SEP-837 `application_type` rules.

    `application_type` governs which redirect URIs a Dynamically Registered
    Client may use (RFC 7591 §2, OpenID Connect Dynamic Client Registration §2):

    - `"web"` clients must use `https` redirect URIs on a non-loopback host.
      Loopback `http`, `https://localhost`, and app/custom schemes are rejected.
      This is the restriction SEP-837 actually asks for.
    - `"native"` clients keep every scheme FastMCP already allowed, except that
      `http` is restricted to loopback hosts (RFC 8252 §7.3, any port). App and
      private-use schemes pass through untouched: `vscode://`,
      `com.example.app:/callback`, `myapp://callback`, `urn:ietf:wg:oauth:2.0:oob`.

    Deliberately absent: any attempt to classify a native client's scheme as
    "private-use" versus "a network transport". There is no sound test. The IANA
    registry cannot separate them — `vscode` is registered *because* it is an
    app-dispatch scheme, alongside transports like `coap` and `smb` — and
    reverse-domain notation fails too, since `iris.beep` and
    `microsoft.windows.camera` are registered while `myapp` is not. Every
    formulation either rejects schemes real MCP clients depend on or admits the
    ones it meant to exclude, so native scheme filtering is left to the
    unsafe-scheme check below.

    Unsafe browser schemes (`javascript:`, `data:`, `file:`, `vbscript:`) are
    always rejected regardless of `application_type`. That check predates this
    function and is unchanged by it.

    The MCP SDK defaults `application_type` to `"native"` because MCP clients
    typically register loopback redirect URIs, so omitting the field preserves
    the behavior clients relied on before this check existed. `None` — which a
    registered-client record carries when the field was never set — is treated
    the same way.
    """
    uri_str = str(redirect_uri)

    if _is_unsafe_redirect_uri(uri_str):
        return False

    parsed = urlparse(uri_str)
    scheme = parsed.scheme.lower()

    if application_type == "web":
        # "web": require an https redirect URI on a non-loopback host.
        if scheme != "https":
            return False
        return not is_loopback_host(parsed.hostname)

    # "native" (and the SDK default): cleartext http only to a loopback host;
    # every other non-unsafe scheme is left alone.
    if scheme == "http":
        return is_loopback_host(parsed.hostname)
    return True


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
