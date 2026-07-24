"""SSRF-safe HTTP utilities for FastMCP.

This module provides SSRF-protected HTTP fetching with:
- DNS resolution and IP validation before requests
- DNS pinning to prevent rebinding TOCTOU attacks
- Support for both CIMD and JWKS fetches

When ``FASTMCP_SSRF_TRUST_PROXY`` is set, DNS resolution and the IP blocklist are
skipped and a single request is made to the hostname URL through the configured
HTTPS_PROXY/ALL_PROXY, delegating DNS and egress to that trusted proxy (the scheme
and hostname checks still apply). The proxy URL is read from the environment and
passed to httpx2 explicitly with ``trust_env`` disabled, so the request is provably
routed through the proxy rather than predicted to be — NO_PROXY is not evaluated in
this mode. If no proxy is configured, the fetch is refused rather than sent direct
with the blocklist disabled.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx2

import fastmcp
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

NAT64_PREFIXES: tuple[
    tuple[ipaddress.IPv6Network, tuple[tuple[int, int, int, int], ...]], ...
] = (
    (ipaddress.IPv6Network("64:ff9b::/96"), ((12, 13, 14, 15),)),
    (
        ipaddress.IPv6Network("64:ff9b:1::/48"),
        (
            (6, 7, 9, 10),
            (7, 9, 10, 11),
            (9, 10, 11, 12),
            (12, 13, 14, 15),
        ),
    ),
)
LOW32_OFFSETS = (12, 13, 14, 15)
IPV4_TRANSLATED_PREFIX = ipaddress.IPv6Network("0:0:0:0:ffff:0:0:0/96")
ISATAP_INTERFACE_IDS = (b"\x00\x00\x5e\xfe", b"\x02\x00\x5e\xfe")


def format_ip_for_url(ip_str: str) -> str:
    """Format IP address for use in URL (bracket IPv6 addresses).

    IPv6 addresses must be bracketed in URLs to distinguish the address from
    the port separator. For example: https://[2001:db8::1]:443/path

    Args:
        ip_str: IP address string

    Returns:
        IP string suitable for URL (IPv6 addresses are bracketed)
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        if isinstance(ip, ipaddress.IPv6Address):
            return f"[{ip_str}]"
        return ip_str
    except ValueError:
        return ip_str


def _configured_proxy_url() -> str | None:
    """Return the proxy URL to route proxy-trust fetches through, if any is set.

    Reads ``HTTPS_PROXY``/``https_proxy`` first, falling back to ``ALL_PROXY``/
    ``all_proxy``. This is a simple presence check: no host matching, no ``NO_PROXY``
    evaluation. The caller passes the returned URL to httpx2 explicitly (with
    ``trust_env`` disabled) so there is no routing decision left for httpx2 to make
    differently than this function assumed — see the module docstring and
    :func:`validate_url` for why that matters.

    Returns:
        The configured proxy URL, or None if none of the supported variables are set.
    """
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name)
        if value:
            return value
    return None


class SSRFError(Exception):
    """Raised when an SSRF protection check fails."""


class SSRFFetchError(Exception):
    """Raised when SSRF-safe fetch fails."""


def _embedded_ipv4_addresses(
    ip: ipaddress.IPv6Address,
) -> set[ipaddress.IPv4Address]:
    """Return IPv4 addresses embedded in known IPv6 transition forms."""
    candidates: set[ipaddress.IPv4Address] = set()
    packed = ip.packed

    def from_offsets(offsets: tuple[int, int, int, int]) -> ipaddress.IPv4Address:
        return ipaddress.IPv4Address(bytes(packed[i] for i in offsets))

    if ip.ipv4_mapped:
        candidates.add(ip.ipv4_mapped)
    if ip.sixtofour:
        candidates.add(ip.sixtofour)
    if ip.teredo:
        server, client = ip.teredo
        candidates.update((server, client))
    if ip in IPV4_TRANSLATED_PREFIX:
        candidates.add(from_offsets(LOW32_OFFSETS))

    for prefix, offset_options in NAT64_PREFIXES:
        if ip in prefix:
            candidates.update(from_offsets(offsets) for offsets in offset_options)

    if int(ip) >> 32 == 0 and not ip.is_loopback and not ip.is_unspecified:
        candidates.add(from_offsets(LOW32_OFFSETS))

    if packed[8:12] in ISATAP_INTERFACE_IDS:
        candidates.add(from_offsets(LOW32_OFFSETS))

    return candidates


def is_ip_allowed(ip_str: str) -> bool:
    """Check if an IP address is allowed (must be globally routable unicast).

    Uses ip.is_global which catches:
    - Private (10.x, 172.16-31.x, 192.168.x)
    - Loopback (127.x, ::1)
    - Link-local (169.254.x, fe80::) - includes AWS metadata!
    - Reserved, unspecified
    - RFC6598 Carrier-Grade NAT (100.64.0.0/10) - can point to internal networks
    - IPv6 transition forms that embed blocked IPv4 targets

    Additionally blocks multicast addresses (not caught by is_global).

    Args:
        ip_str: IP address string to check

    Returns:
        True if the IP is allowed (public unicast internet), False if blocked
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if isinstance(ip, ipaddress.IPv6Address):
        if any(
            not is_ip_allowed(str(embedded_ip))
            for embedded_ip in _embedded_ipv4_addresses(ip)
        ):
            return False

    if not ip.is_global:
        return False

    # Block multicast (not caught by is_global for some ranges)
    return not ip.is_multicast


async def resolve_hostname(hostname: str, port: int = 443) -> list[str]:
    """Resolve hostname to IP addresses using DNS.

    Args:
        hostname: Hostname to resolve
        port: Port number (used for getaddrinfo)

    Returns:
        List of resolved IP addresses

    Raises:
        SSRFError: If resolution fails
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(
                hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM
            ),
        )
        ips = list({info[4][0] for info in infos})
        if not ips:
            raise SSRFError(f"DNS resolution returned no addresses for {hostname}")
        return ips  # ty: ignore[invalid-return-type]
    except socket.gaierror as e:
        raise SSRFError(f"DNS resolution failed for {hostname}: {e}") from e


@dataclass
class ValidatedURL:
    """A URL that has been validated for SSRF with resolved IPs."""

    original_url: str
    hostname: str
    port: int
    path: str
    resolved_ips: list[str]
    proxy_url: str | None = None


@dataclass
class SSRFFetchResponse:
    """Response payload from an SSRF-safe fetch."""

    content: bytes
    status_code: int
    headers: dict[str, str]


@dataclass
class _FetchTarget:
    """A single connection attempt for an SSRF-safe fetch.

    In pinned (default) mode there is one target per resolved IP: the request goes to
    an IP-literal URL with Host and SNI pinned to the validated hostname. In proxy
    mode (FASTMCP_SSRF_TRUST_PROXY) there is a single target: the original hostname
    URL with no pinning and an explicit ``proxy_url``, so the request is dialed
    through the trusted proxy and the proxy (not httpx's environment-proxy routing)
    owns DNS and TLS.
    """

    url: str
    host_header: str | None
    sni_hostname: str | None
    proxy_url: str | None = None


def _build_fetch_targets(validated: ValidatedURL) -> list[_FetchTarget]:
    """Build the ordered connection attempts for a validated URL.

    An empty ``resolved_ips`` means proxy mode (see :func:`validate_url`): a single
    unpinned request to the original hostname URL, explicitly routed through
    ``validated.proxy_url``. Otherwise, one pinned IP-literal request per resolved
    IP, tried in order with fallback on connection error.
    """
    if not validated.resolved_ips:
        # Proxy mode: dial the original hostname URL verbatim and let the proxy parse
        # and resolve it. validated.hostname is informational here — it does not
        # constrain what gets dialed — so do not pin Host or SNI from it.
        return [
            _FetchTarget(
                url=validated.original_url,
                host_header=None,
                sni_hostname=None,
                proxy_url=validated.proxy_url,
            )
        ]

    return [
        _FetchTarget(
            url=f"https://{format_ip_for_url(ip)}:{validated.port}{validated.path}",
            host_header=validated.hostname,
            sni_hostname=validated.hostname,
        )
        for ip in validated.resolved_ips
    ]


async def validate_url(url: str, require_path: bool = False) -> ValidatedURL:
    """Validate URL for SSRF and resolve to IPs.

    Args:
        url: URL to validate
        require_path: If True, require non-root path (for CIMD)

    Returns:
        ValidatedURL with resolved IPs

    Raises:
        SSRFError: If the URL is invalid, resolves to blocked IPs, or proxy-trust
            mode is enabled but no configured proxy will route the request.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError) as e:
        raise SSRFError(f"Invalid URL: {e}") from e

    if parsed.scheme != "https":
        raise SSRFError(f"URL must use HTTPS, got: {parsed.scheme}")

    if not parsed.netloc:
        raise SSRFError("URL must have a host")

    if require_path and parsed.path in ("", "/"):
        raise SSRFError("URL must have a non-root path")

    hostname = parsed.hostname or parsed.netloc
    port = parsed.port or 443
    path = parsed.path + ("?" + parsed.query if parsed.query else "")

    # Proxy mode (FASTMCP_SSRF_TRUST_PROXY): a trusted outbound proxy owns DNS and
    # egress, so resolving the hostname here is pointless — the IP we'd pin is not
    # the one the proxy dials, making the blocklist unenforceable theater. Skip
    # resolution and the blocklist entirely and signal proxy mode downstream with an
    # empty resolved_ips list. The scheme (HTTPS) and host checks above still run.
    if fastmcp.settings.ssrf_trust_proxy:
        # Skipping the blocklist is only safe if the request is *actually* routed
        # through a trusted proxy, so this does not try to predict whether it will
        # be — it controls it. Earlier revisions predicted httpx2's routing decision,
        # first by approximating NO_PROXY handling with urllib.request.proxy_bypass(),
        # then by replicating httpx2's own get_environment_proxies()/URLPattern
        # matching internally. Both were still predictions of a library with
        # open-ended NO_PROXY semantics, and each was found wrong for a different
        # NO_PROXY form (port-qualified, IPv6, scheme-qualified entries each broke a
        # different revision) — always in the dangerous direction of assuming
        # "proxied" for a request that actually went out direct.
        #
        # Instead, read the proxy URL directly from the environment and hand it to
        # httpx2 explicitly below, with trust_env disabled. With an explicit
        # `proxy=` and `trust_env=False`, httpx2 has no routing decision left to make
        # differently than assumed here: the request provably goes through that
        # proxy or the connection fails. NO_PROXY is therefore not evaluated in this
        # mode at all — a NO_PROXY'd host is routed through the proxy rather than
        # fetched direct with the blocklist already disabled, which is strictly safer
        # than the alternative (see the module docstring). If no proxy is configured,
        # there is nothing to route through, so refuse rather than fetch unprotected.
        proxy_url = _configured_proxy_url()
        if proxy_url is None:
            raise SSRFError(
                f"FASTMCP_SSRF_TRUST_PROXY is enabled but no HTTPS_PROXY/ALL_PROXY is "
                f"configured, so the request to {hostname} would go direct with SSRF "
                f"protection disabled. Set HTTPS_PROXY (or ALL_PROXY) to the trusted "
                f"proxy, or unset FASTMCP_SSRF_TRUST_PROXY to restore DNS/IP "
                f"validation."
            )
        return ValidatedURL(
            original_url=url,
            hostname=hostname,
            port=port,
            path=path,
            resolved_ips=[],
            proxy_url=proxy_url,
        )

    # Resolve and validate IPs (resolve_hostname raises rather than returning [], so a
    # successful return here always yields a non-empty list — see ssrf_safe_fetch_response).
    resolved_ips = await resolve_hostname(hostname, port)

    blocked = [ip for ip in resolved_ips if not is_ip_allowed(ip)]
    if blocked:
        raise SSRFError(
            f"URL resolves to blocked IP address(es): {blocked}. "
            f"Private, loopback, link-local, and reserved IPs are not allowed."
        )

    return ValidatedURL(
        original_url=url,
        hostname=hostname,
        port=port,
        path=path,
        resolved_ips=resolved_ips,
    )


async def ssrf_safe_fetch(
    url: str,
    *,
    require_path: bool = False,
    max_size: int = 5120,
    timeout: float = 10.0,
    overall_timeout: float = 30.0,
) -> bytes:
    """Fetch URL with comprehensive SSRF protection and DNS pinning.

    Security measures:
    1. HTTPS only
    2. DNS resolution with IP validation
    3. Connects to validated IP directly (DNS pinning prevents rebinding)
    4. Response size limit
    5. Redirects disabled
    6. Overall timeout

    Args:
        url: URL to fetch
        require_path: If True, require non-root path
        max_size: Maximum response size in bytes (default 5KB)
        timeout: Per-operation timeout in seconds
        overall_timeout: Overall timeout for entire operation

    Returns:
        Response body as bytes

    Raises:
        SSRFError: If SSRF validation fails
        SSRFFetchError: If fetch fails
    """
    response = await ssrf_safe_fetch_response(
        url,
        require_path=require_path,
        max_size=max_size,
        timeout=timeout,
        overall_timeout=overall_timeout,
        allowed_status_codes={200},
    )
    return response.content


async def ssrf_safe_fetch_response(
    url: str,
    *,
    require_path: bool = False,
    max_size: int = 5120,
    timeout: float = 10.0,
    overall_timeout: float = 30.0,
    request_headers: Mapping[str, str] | None = None,
    allowed_status_codes: set[int] | None = None,
) -> SSRFFetchResponse:
    """Fetch URL with SSRF protection and return response metadata.

    This is equivalent to :func:`ssrf_safe_fetch` but returns response headers
    and status code, and supports conditional request headers.
    """
    start_time = time.monotonic()

    # Validate URL and resolve DNS
    validated = await validate_url(url, require_path=require_path)

    last_error: Exception | None = None
    expected_statuses = allowed_status_codes or {200}

    # One target per pinned IP in default mode; a single unpinned target in proxy mode.
    targets = _build_fetch_targets(validated)

    for target in targets:
        elapsed = time.monotonic() - start_time
        if elapsed > overall_timeout:
            raise SSRFFetchError(f"Overall timeout exceeded: {url}")
        remaining = max(1.0, overall_timeout - elapsed)

        logger.debug("SSRF-safe fetch: %s -> %s", url, target.url)

        # In pinned mode Host is forced to the validated hostname; in proxy mode httpx
        # derives it from the hostname URL. Either way, never let a caller override it.
        headers: dict[str, str] = {}
        if target.host_header is not None:
            headers["Host"] = target.host_header
        if request_headers:
            for key, value in request_headers.items():
                if key.lower() == "host":
                    continue
                headers[key] = value

        # Pin SNI to the hostname when connecting to an IP literal; in proxy mode httpx
        # derives SNI from the URL, so no override is sent.
        extensions: dict[str, str] = {}
        if target.sni_hostname is not None:
            extensions["sni_hostname"] = target.sni_hostname

        try:
            # Use httpx with streaming to enforce size limit during download
            async with (
                httpx2.AsyncClient(
                    timeout=httpx2.Timeout(
                        connect=min(timeout, remaining),
                        read=min(timeout, remaining),
                        write=min(timeout, remaining),
                        pool=min(timeout, remaining),
                    ),
                    follow_redirects=False,
                    verify=True,
                    # Default (pinned) mode has no proxy_url and keeps trust_env's
                    # normal default (True). Proxy-trust mode sets an explicit
                    # proxy_url and turns trust_env off, so httpx2 has no environment
                    # -based routing decision left to make — see validate_url() above
                    # for why that matters.
                    proxy=target.proxy_url,
                    trust_env=target.proxy_url is None,
                ) as client,
                client.stream(
                    "GET",
                    target.url,
                    headers=headers,
                    extensions=extensions,
                ) as response,
            ):
                if time.monotonic() - start_time > overall_timeout:
                    raise SSRFFetchError(f"Overall timeout exceeded: {url}")

                if response.status_code not in expected_statuses:
                    raise SSRFFetchError(f"HTTP {response.status_code} fetching {url}")

                # Check Content-Length header first if available
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        size = int(content_length)
                        if size > max_size:
                            raise SSRFFetchError(
                                f"Response too large: {size} bytes (max {max_size})"
                            )
                    except ValueError:
                        pass

                # Stream the response and enforce size limit during download
                chunks = []
                total = 0
                async for chunk in response.aiter_bytes():
                    if time.monotonic() - start_time > overall_timeout:
                        raise SSRFFetchError(f"Overall timeout exceeded: {url}")
                    total += len(chunk)
                    if total > max_size:
                        raise SSRFFetchError(
                            f"Response too large: exceeded {max_size} bytes"
                        )
                    chunks.append(chunk)

                return SSRFFetchResponse(
                    content=b"".join(chunks),
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )

        except httpx2.TimeoutException as e:
            last_error = e
            continue
        except httpx2.RequestError as e:
            last_error = e
            continue

    if last_error is not None:
        if isinstance(last_error, httpx2.TimeoutException):
            raise SSRFFetchError(f"Timeout fetching {url}") from last_error
        raise SSRFFetchError(f"Error fetching {url}: {last_error}") from last_error

    raise SSRFFetchError(f"Error fetching {url}: no fetch targets succeeded")
