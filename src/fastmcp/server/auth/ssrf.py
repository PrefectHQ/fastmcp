"""SSRF-safe HTTP utilities for FastMCP.

This module provides SSRF-protected HTTP fetching with:
- DNS resolution and IP validation before requests
- DNS pinning to prevent rebinding TOCTOU attacks
- Support for both CIMD and JWKS fetches
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


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


class SSRFError(Exception):
    """Raised when an SSRF protection check fails."""


class SSRFFetchError(Exception):
    """Raised when SSRF-safe fetch fails."""


def is_ip_allowed(ip_str: str) -> bool:
    """Check if an IP address is allowed (must be globally routable unicast).

    Uses ip.is_global which catches:
    - Private (10.x, 172.16-31.x, 192.168.x)
    - Loopback (127.x, ::1)
    - Link-local (169.254.x, fe80::) - includes AWS metadata!
    - Reserved, unspecified
    - RFC6598 Carrier-Grade NAT (100.64.0.0/10) - can point to internal networks

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

    if not ip.is_global:
        return False

    # Block multicast (not caught by is_global for some ranges)
    if ip.is_multicast:
        return False

    # IPv6-specific checks for embedded IPv4 addresses
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return is_ip_allowed(str(ip.ipv4_mapped))
        if ip.sixtofour:
            return is_ip_allowed(str(ip.sixtofour))
        if ip.teredo:
            server, client = ip.teredo
            return is_ip_allowed(str(server)) and is_ip_allowed(str(client))

    return True


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
        return ips
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


async def validate_url(url: str, require_path: bool = False) -> ValidatedURL:
    """Validate URL for SSRF and resolve to IPs.

    Args:
        url: URL to validate
        require_path: If True, require non-root path (for CIMD)

    Returns:
        ValidatedURL with resolved IPs

    Raises:
        SSRFError: If URL is invalid or resolves to blocked IPs
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

    # Resolve and validate IPs
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
        path=parsed.path + ("?" + parsed.query if parsed.query else ""),
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

    for pinned_ip in validated.resolved_ips:
        elapsed = time.monotonic() - start_time
        if elapsed > overall_timeout:
            raise SSRFFetchError(f"Overall timeout exceeded: {url}")
        remaining = max(1.0, overall_timeout - elapsed)

        pinned_url = (
            f"https://{format_ip_for_url(pinned_ip)}:{validated.port}{validated.path}"
        )

        logger.debug(
            "SSRF-safe fetch: %s -> %s (pinned to %s)",
            url,
            pinned_url,
            pinned_ip,
        )

        headers = {"Host": validated.hostname}
        if request_headers:
            for key, value in request_headers.items():
                # Host must remain pinned to the validated hostname.
                if key.lower() == "host":
                    continue
                headers[key] = value

        try:
            # Use httpx with streaming to enforce size limit during download
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        connect=min(timeout, remaining),
                        read=min(timeout, remaining),
                        write=min(timeout, remaining),
                        pool=min(timeout, remaining),
                    ),
                    follow_redirects=False,
                    verify=True,
                ) as client,
                client.stream(
                    "GET",
                    pinned_url,
                    headers=headers,
                    extensions={"sni_hostname": validated.hostname},
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

        except httpx.TimeoutException as e:
            last_error = e
            continue
        except httpx.RequestError as e:
            last_error = e
            continue

    if last_error is not None:
        if isinstance(last_error, httpx.TimeoutException):
            raise SSRFFetchError(f"Timeout fetching {url}") from last_error
        raise SSRFFetchError(f"Error fetching {url}: {last_error}") from last_error

    raise SSRFFetchError(f"Error fetching {url}: no resolved IPs succeeded")
