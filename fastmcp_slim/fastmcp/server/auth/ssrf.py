"""SSRF-safe HTTP utilities for FastMCP.

This module provides SSRF-protected HTTP fetching with:
- DNS resolution and IP validation before requests
- DNS pinning to prevent rebinding TOCTOU attacks
- Support for both CIMD and JWKS fetches

When ``FASTMCP_SSRF_TRUST_PROXY`` is set, DNS resolution and the IP blocklist are
skipped and a single request is made to the hostname URL, delegating DNS and egress
to a trusted outbound proxy (the scheme and hostname checks still apply). If no
configured proxy would route the request — no HTTPS/ALL proxy, or NO_PROXY excludes
the host — the fetch is refused rather than sent direct with the blocklist disabled.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.request import getproxies, proxy_bypass

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


def _proxy_bypass_target(hostname: str, port: int) -> str:
    """Build the host argument passed to ``urllib.request.proxy_bypass()``.

    httpx2 honors port-qualified ``NO_PROXY`` entries for IPv4 addresses and domain
    names (``NO_PROXY=127.0.0.1:8443`` excludes only that port), so the port must be
    included here or a port-specific bypass rule slips past ``proxy_bypass()``
    undetected — it would report "not bypassed" for a request httpx2 actually sends
    direct. Verified empirically against both ``proxy_bypass()`` and httpx2's own
    ``get_environment_proxies()``/``URLPattern`` routing.

    IPv6 is handled separately: httpx2 never attaches a port when it turns an IPv6
    ``NO_PROXY`` entry into a routing rule — it brackets the bare address and matches
    on host alone, so such an entry excludes that host on every port. Reproducing that
    with a port suffix does not work through ``proxy_bypass()``: the bracketed form
    (``[addr]:port``) never matches any ``NO_PROXY`` entry (``urllib``'s own NO_PROXY
    parser does not understand brackets), and the unbracketed form (``addr:port``) is
    ambiguous with a literal IPv6 address that legitimately ends in decimal digits.
    The bare hostname is therefore not a weaker fallback for IPv6 — it is the
    construction that actually matches httpx2's real routing, confirmed against
    ``get_environment_proxies()``/``URLPattern`` the same way as the IPv4 case above.

    Args:
        hostname: Hostname or IP literal from the parsed URL (no brackets, no port).
        port: Port the request will be made to.

    Returns:
        The string to pass to ``proxy_bypass()``.
    """
    try:
        is_ipv6 = isinstance(ipaddress.ip_address(hostname), ipaddress.IPv6Address)
    except ValueError:
        is_ipv6 = False
    return hostname if is_ipv6 else f"{hostname}:{port}"


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
    URL with no pinning, so httpx and the trusted proxy own DNS and TLS.
    """

    url: str
    host_header: str | None
    sni_hostname: str | None


def _build_fetch_targets(validated: ValidatedURL) -> list[_FetchTarget]:
    """Build the ordered connection attempts for a validated URL.

    An empty ``resolved_ips`` means proxy mode (see :func:`validate_url`): a single
    unpinned request to the original hostname URL, which httpx routes through the
    configured proxy. Otherwise, one pinned IP-literal request per resolved IP, tried
    in order with fallback on connection error.
    """
    if not validated.resolved_ips:
        # Proxy mode: dial the original hostname URL verbatim and let httpx + the proxy
        # parse and resolve it. validated.hostname is informational here — it does not
        # constrain what gets dialed — so do not pin Host or SNI from it.
        return [
            _FetchTarget(
                url=validated.original_url,
                host_header=None,
                sni_hostname=None,
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
        # through the trusted proxy, so confirm it will be before proceeding: an HTTPS
        # request routes only through the https/all proxy entries, and NO_PROXY can
        # still exclude this host. getproxies() ignores NO_PROXY, so consult
        # proxy_bypass() too. This approximates httpx2's own trust_env routing, not an
        # exact copy of it, and one gap has already bitten us in production: httpx2
        # honors port-qualified NO_PROXY entries (e.g. NO_PROXY=127.0.0.1:8443 excludes
        # only that port), so proxy_bypass() must be called with "{hostname}:{port}",
        # not the bare hostname — a bare-hostname call misses the port qualifier and
        # concludes "proxied" for a target httpx2 actually sends direct. IPv6 is
        # handled separately below: httpx2 never attaches a port when it parses an
        # IPv6 NO_PROXY entry, and neither the bracketed nor the port-suffixed form of
        # an IPv6 literal reliably matches through proxy_bypass()'s own NO_PROXY
        # parser, so IPv6 targets are checked by bare address instead — see
        # _proxy_bypass_target() for the verified behavior behind that split. The
        # intended contract is that every remaining divergence between this check and
        # httpx2's real routing errs closed (we refuse a fetch httpx2 would have
        # proxied, never the reverse); that must be re-verified whenever this function
        # or the helper below changes, since it does not fall out of the code
        # automatically. If nothing will proxy this target the fetch would otherwise go
        # out direct with the blocklist already disabled — no SSRF protection at all —
        # so refuse rather than make an unprotected request. The setting's contract is
        # crisp: proxy-trust mode delegates SSRF protection to the proxy, and with no
        # proxy for this target the fetch cannot proceed.
        proxies = getproxies()
        if "https" not in proxies and "all" not in proxies:
            raise SSRFError(
                f"FASTMCP_SSRF_TRUST_PROXY is enabled but no HTTPS_PROXY/ALL_PROXY is "
                f"configured, so the request to {hostname} would go direct with SSRF "
                f"protection disabled. Point HTTPS_PROXY at the trusted proxy, or "
                f"unset FASTMCP_SSRF_TRUST_PROXY to restore DNS/IP validation."
            )
        bypass_target = _proxy_bypass_target(hostname, port)
        if proxy_bypass(bypass_target):
            raise SSRFError(
                f"FASTMCP_SSRF_TRUST_PROXY is enabled and a proxy is configured, but "
                f"NO_PROXY (or a proxy-bypass rule) excludes {bypass_target}, so the "
                f"request would go direct with SSRF protection disabled. Remove "
                f"{bypass_target} from NO_PROXY to route it through the proxy, or "
                f"unset FASTMCP_SSRF_TRUST_PROXY to restore DNS/IP validation."
            )
        return ValidatedURL(
            original_url=url,
            hostname=hostname,
            port=port,
            path=path,
            resolved_ips=[],
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
                    # Proxy mode relies on httpx reading HTTPS_PROXY/HTTP_PROXY from the
                    # environment; keep trust_env on (the default) so that routing works.
                    # Disabling it would silently turn proxy-mode fetches into direct,
                    # unpinned requests.
                    trust_env=True,
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
