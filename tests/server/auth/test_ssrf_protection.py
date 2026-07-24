"""Tests for SSRF-safe HTTP utilities.

This module tests the ssrf.py module which provides SSRF-protected HTTP fetching.
"""

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest

import fastmcp
from fastmcp.server.auth.ssrf import (
    SSRFError,
    SSRFFetchError,
    is_ip_allowed,
    ssrf_safe_fetch,
    validate_url,
)
from fastmcp.utilities.tests import temporary_settings


def _mock_httpx_client(
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    body_chunks: list[bytes] | None = None,
) -> AsyncMock:
    """Build a mock httpx2.AsyncClient whose stream() yields a canned response.

    The returned client's ``.stream.call_args`` exposes the request that was made.
    """
    if headers is None:
        headers = {"content-length": "2"}
    if body_chunks is None:
        body_chunks = [b"ok"]

    mock_stream = MagicMock()
    mock_stream.status_code = status_code
    mock_stream.headers = headers
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)

    async def aiter_bytes():
        for chunk in body_chunks:
            yield chunk

    mock_stream.aiter_bytes = aiter_bytes

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


class TestIsIPAllowed:
    """Tests for is_ip_allowed function."""

    def test_public_ipv4_allowed(self):
        """Public IPv4 addresses should be allowed."""
        assert is_ip_allowed("8.8.8.8") is True
        assert is_ip_allowed("1.1.1.1") is True
        assert is_ip_allowed("93.184.216.34") is True

    def test_private_ipv4_blocked(self):
        """Private IPv4 addresses should be blocked."""
        assert is_ip_allowed("192.168.1.1") is False
        assert is_ip_allowed("10.0.0.1") is False
        assert is_ip_allowed("172.16.0.1") is False

    def test_loopback_blocked(self):
        """Loopback addresses should be blocked."""
        assert is_ip_allowed("127.0.0.1") is False
        assert is_ip_allowed("::1") is False

    def test_link_local_blocked(self):
        """Link-local addresses (AWS metadata) should be blocked."""
        assert is_ip_allowed("169.254.169.254") is False

    def test_rfc6598_cgnat_blocked(self):
        """RFC6598 Carrier-Grade NAT addresses should be blocked."""
        assert is_ip_allowed("100.64.0.1") is False
        assert is_ip_allowed("100.100.100.100") is False

    def test_ipv4_mapped_ipv6_blocked_if_private(self):
        """IPv4-mapped IPv6 addresses should check the embedded IPv4."""
        assert is_ip_allowed("::ffff:127.0.0.1") is False
        assert is_ip_allowed("::ffff:192.168.1.1") is False

    @pytest.mark.parametrize(
        "address",
        [
            pytest.param("64:ff9b::7f00:1", id="nat64-loopback"),
            pytest.param("64:ff9b::0a00:1", id="nat64-private"),
            pytest.param("64:ff9b::a9fe:a9fe", id="nat64-link-local"),
            pytest.param("64:ff9b::6440:1", id="nat64-cgnat"),
            pytest.param("64:ff9b:1::a9fe:a9fe", id="nat64-local-use-low32"),
            pytest.param("64:ff9b:1:a9fe:a9:fe00::", id="nat64-local-use-48"),
            pytest.param("::ffff:0:7f00:1", id="ipv4-translated-loopback"),
            pytest.param("::ffff:0:0a00:1", id="ipv4-translated-private"),
            pytest.param("::ffff:0:a9fe:a9fe", id="ipv4-translated-link-local"),
            pytest.param("::ffff:0:6440:1", id="ipv4-translated-cgnat"),
            pytest.param("::7f00:1", id="ipv4-compatible-loopback"),
            pytest.param("::0a00:1", id="ipv4-compatible-private"),
            pytest.param("::a9fe:a9fe", id="ipv4-compatible-link-local"),
            pytest.param("::6440:1", id="ipv4-compatible-cgnat"),
            pytest.param("2002:a9fe:a9fe::1", id="6to4-link-local"),
            pytest.param("2606:4700::5efe:192.168.1.1", id="isatap-private"),
            pytest.param(
                "2606:4700::200:5efe:169.254.169.254",
                id="isatap-link-local",
            ),
        ],
    )
    def test_ipv6_transition_blocked_if_embedded_ipv4_blocked(self, address: str):
        """IPv6 transition addresses should check the embedded IPv4."""
        assert is_ip_allowed(address) is False

    @pytest.mark.parametrize(
        "address",
        [
            pytest.param("64:ff9b::0808:0808", id="nat64"),
            pytest.param("::ffff:0:0808:0808", id="ipv4-translated"),
            pytest.param("::0808:0808", id="ipv4-compatible"),
            pytest.param("2606:4700::5efe:8.8.8.8", id="isatap"),
        ],
    )
    def test_ipv6_transition_allowed_if_embedded_ipv4_allowed(self, address: str):
        """IPv6 transition addresses should allow public embedded IPv4."""
        assert is_ip_allowed(address) is True


class TestValidateURL:
    """Tests for validate_url function."""

    async def test_http_rejected(self):
        """HTTP URLs should be rejected (HTTPS required)."""
        with pytest.raises(SSRFError, match="must use HTTPS"):
            await validate_url("http://example.com/path")

    async def test_missing_host_rejected(self):
        """URLs without host should be rejected."""
        with pytest.raises(SSRFError, match="must have a host"):
            await validate_url("https:///path")

    async def test_root_path_rejected_when_required(self):
        """Root paths should be rejected when require_path=True."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=["93.184.216.34"],
        ):
            with pytest.raises(SSRFError, match="non-root path"):
                await validate_url("https://example.com/", require_path=True)

    async def test_private_ip_rejected(self):
        """URLs resolving to private IPs should be rejected."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=["192.168.1.1"],
        ):
            with pytest.raises(SSRFError, match="blocked IP"):
                await validate_url("https://example.com/path")

    @pytest.mark.parametrize(
        "address",
        [
            pytest.param("64:ff9b::0a00:1", id="nat64"),
            pytest.param("64:ff9b:1:a9fe:a9:fe00::", id="nat64-local-use"),
            pytest.param("::ffff:0:a9fe:a9fe", id="ipv4-translated"),
            pytest.param("::a9fe:a9fe", id="ipv4-compatible"),
            pytest.param("2606:4700::5efe:169.254.169.254", id="isatap"),
        ],
    )
    async def test_ipv6_transition_private_ip_rejected(self, address: str):
        """URLs resolving to IPv6-wrapped private IPs should be rejected."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=[address],
        ):
            with pytest.raises(SSRFError, match="blocked IP"):
                await validate_url("https://example.com/path")


class TestSSRFSafeFetch:
    """Tests for ssrf_safe_fetch function."""

    async def test_private_ip_blocked(self):
        """Fetch to private IP should be blocked."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=["192.168.1.1"],
        ):
            with pytest.raises(SSRFError, match="blocked IP"):
                await ssrf_safe_fetch("https://internal.example.com/api")

    async def test_cgnat_blocked(self):
        """Fetch to RFC6598 CGNAT IP should be blocked."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=["100.64.0.1"],
        ):
            with pytest.raises(SSRFError, match="blocked IP"):
                await ssrf_safe_fetch("https://cgnat.example.com/api")

    async def test_connects_to_pinned_ip(self):
        """Verify connection uses pinned IP, not re-resolved DNS."""
        resolved_ip = "93.184.216.34"

        with (
            patch(
                "fastmcp.server.auth.ssrf.resolve_hostname",
                return_value=[resolved_ip],
            ),
            patch("httpx2.AsyncClient") as mock_client_class,
        ):
            mock_stream = MagicMock()
            mock_stream.status_code = 200
            mock_stream.headers = {"content-length": "15"}
            mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream.__aexit__ = AsyncMock(return_value=None)

            async def aiter_bytes():
                yield b'{"data": "test"}'

            mock_stream.aiter_bytes = aiter_bytes

            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            await ssrf_safe_fetch("https://example.com/api")

            # Verify URL contains pinned IP
            call_args = mock_client.stream.call_args
            url_called = call_args[0][1]
            assert resolved_ip in url_called

    async def test_fallback_to_second_ip(self):
        """If the first IP fails, the next resolved IP should be tried."""
        resolved_ips = ["2001:4860:4860::8888", "93.184.216.34"]

        with (
            patch(
                "fastmcp.server.auth.ssrf.resolve_hostname",
                return_value=resolved_ips,
            ),
            patch("httpx2.AsyncClient") as mock_client_class,
        ):
            request = httpx2.Request("GET", "https://example.com/api")

            first_client = AsyncMock()
            first_client.stream = MagicMock(
                side_effect=httpx2.RequestError("boom", request=request)
            )
            first_client.__aenter__.return_value = first_client
            first_client.__aexit__ = AsyncMock(return_value=None)

            mock_stream = MagicMock()
            mock_stream.status_code = 200
            mock_stream.headers = {"content-length": "2"}
            mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream.__aexit__ = AsyncMock(return_value=None)

            async def aiter_bytes():
                yield b"ok"

            mock_stream.aiter_bytes = aiter_bytes

            second_client = AsyncMock()
            second_client.stream = MagicMock(return_value=mock_stream)
            second_client.__aenter__.return_value = second_client
            second_client.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.side_effect = [first_client, second_client]

            content = await ssrf_safe_fetch("https://example.com/api")
            assert content == b"ok"

            call_args = second_client.stream.call_args
            url_called = call_args[0][1]
            assert resolved_ips[1] in url_called

    async def test_host_header_set(self):
        """Verify Host header is set to original hostname."""
        resolved_ip = "93.184.216.34"
        original_host = "example.com"

        with (
            patch(
                "fastmcp.server.auth.ssrf.resolve_hostname",
                return_value=[resolved_ip],
            ),
            patch("httpx2.AsyncClient") as mock_client_class,
        ):
            mock_stream = MagicMock()
            mock_stream.status_code = 200
            mock_stream.headers = {"content-length": "15"}
            mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream.__aexit__ = AsyncMock(return_value=None)

            async def aiter_bytes():
                yield b'{"data": "test"}'

            mock_stream.aiter_bytes = aiter_bytes

            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            await ssrf_safe_fetch(f"https://{original_host}/api")

            # Verify Host header
            call_kwargs = mock_client.stream.call_args[1]
            assert call_kwargs["headers"]["Host"] == original_host

    async def test_response_size_limit(self):
        """Verify response size limit is enforced via streaming."""
        with (
            patch(
                "fastmcp.server.auth.ssrf.resolve_hostname",
                return_value=["93.184.216.34"],
            ),
            patch("httpx2.AsyncClient") as mock_client_class,
        ):
            # Response larger than default 5KB (no Content-Length, so streaming enforces)
            mock_stream = MagicMock()
            mock_stream.status_code = 200
            mock_stream.headers = {}  # No Content-Length to force streaming check
            mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream.__aexit__ = AsyncMock(return_value=None)

            async def aiter_bytes():
                # Yield 10KB total
                for _ in range(10):
                    yield b"x" * 1024

            mock_stream.aiter_bytes = aiter_bytes

            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(SSRFFetchError, match="too large"):
                await ssrf_safe_fetch("https://example.com/api")


class TestJWKSSSRFProtection:
    """Tests for SSRF protection in JWTVerifier JWKS fetching."""

    async def test_jwks_private_ip_blocked(self):
        """JWKS fetch to private IP should be blocked."""
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        verifier = JWTVerifier(
            jwks_uri="https://internal.example.com/.well-known/jwks.json",
            issuer="https://issuer.example.com",
            ssrf_safe=True,
        )

        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=["192.168.1.1"],
        ):
            with pytest.raises(ValueError, match="Failed to fetch JWKS"):
                # Create a dummy token to trigger JWKS fetch
                await verifier._get_jwks_key("test-kid")

    async def test_jwks_cgnat_blocked(self):
        """JWKS fetch to RFC6598 CGNAT IP should be blocked."""
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        verifier = JWTVerifier(
            jwks_uri="https://cgnat.example.com/.well-known/jwks.json",
            issuer="https://issuer.example.com",
            ssrf_safe=True,
        )

        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=["100.64.0.1"],
        ):
            with pytest.raises(ValueError, match="Failed to fetch JWKS"):
                await verifier._get_jwks_key("test-kid")

    async def test_jwks_loopback_blocked(self):
        """JWKS fetch to loopback should be blocked."""
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        verifier = JWTVerifier(
            jwks_uri="https://localhost/.well-known/jwks.json",
            issuer="https://issuer.example.com",
            ssrf_safe=True,
        )

        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=["127.0.0.1"],
        ):
            with pytest.raises(ValueError, match="Failed to fetch JWKS"):
                await verifier._get_jwks_key("test-kid")


class TestIPv6URLFormatting:
    """Tests for proper IPv6 address bracketing in URLs."""

    def test_format_ip_for_url_ipv4(self):
        """IPv4 addresses should not be bracketed."""
        from fastmcp.server.auth.ssrf import format_ip_for_url

        assert format_ip_for_url("8.8.8.8") == "8.8.8.8"
        assert format_ip_for_url("192.168.1.1") == "192.168.1.1"

    def test_format_ip_for_url_ipv6(self):
        """IPv6 addresses should be bracketed for URL use."""
        from fastmcp.server.auth.ssrf import format_ip_for_url

        assert format_ip_for_url("2001:db8::1") == "[2001:db8::1]"
        assert format_ip_for_url("::1") == "[::1]"
        assert format_ip_for_url("fe80::1") == "[fe80::1]"

    def test_format_ip_for_url_invalid(self):
        """Invalid IP strings should be returned unchanged."""
        from fastmcp.server.auth.ssrf import format_ip_for_url

        assert format_ip_for_url("not-an-ip") == "not-an-ip"
        assert format_ip_for_url("") == ""

    async def test_ipv6_pinned_url_is_valid(self):
        """Verify IPv6 addresses are properly bracketed in pinned URLs."""
        resolved_ipv6 = "2001:4860:4860::8888"

        with (
            patch(
                "fastmcp.server.auth.ssrf.resolve_hostname",
                return_value=[resolved_ipv6],
            ),
            patch("httpx2.AsyncClient") as mock_client_class,
        ):
            mock_stream = MagicMock()
            mock_stream.status_code = 200
            mock_stream.headers = {"content-length": "10"}
            mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream.__aexit__ = AsyncMock(return_value=None)

            async def aiter_bytes():
                yield b'{"key": 1}'

            mock_stream.aiter_bytes = aiter_bytes

            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            await ssrf_safe_fetch("https://example.com/api")

            # Verify the URL contains bracketed IPv6 address
            call_args = mock_client.stream.call_args
            url_called = call_args[0][1]

            # IPv6 should be bracketed: https://[2001:4860:4860::8888]:443/path
            assert f"[{resolved_ipv6}]" in url_called, (
                f"Expected bracketed IPv6 [{resolved_ipv6}] in URL, got {url_called}"
            )


class TestStreamingResponseSizeLimit:
    """Tests for streaming-based response size enforcement."""

    async def test_size_limit_enforced_during_streaming(self):
        """Verify that size limit is enforced as chunks are received, not after."""
        with (
            patch(
                "fastmcp.server.auth.ssrf.resolve_hostname",
                return_value=["93.184.216.34"],
            ),
            patch("httpx2.AsyncClient") as mock_client_class,
        ):
            chunks_yielded = []

            async def aiter_bytes():
                # Yield chunks that exceed the limit
                for i in range(10):
                    chunk = b"x" * 1024  # 1KB per chunk
                    chunks_yielded.append(chunk)
                    yield chunk

            mock_stream = MagicMock()
            mock_stream.status_code = 200
            mock_stream.headers = {}  # No content-length to force streaming check
            mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream.__aexit__ = AsyncMock(return_value=None)
            mock_stream.aiter_bytes = aiter_bytes

            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(SSRFFetchError, match="too large"):
                await ssrf_safe_fetch("https://example.com/api", max_size=5120)

            # Verify we stopped after exceeding the limit (should be ~6 chunks for 5KB limit)
            # This confirms we're enforcing during streaming, not after downloading all
            assert len(chunks_yielded) <= 7, (
                f"Downloaded {len(chunks_yielded)} chunks (expected <=7 for streaming enforcement)"
            )

    async def test_content_length_header_checked_first(self):
        """Verify Content-Length header is checked before streaming."""
        with (
            patch(
                "fastmcp.server.auth.ssrf.resolve_hostname",
                return_value=["93.184.216.34"],
            ),
            patch("httpx2.AsyncClient") as mock_client_class,
        ):
            mock_stream = MagicMock()
            mock_stream.status_code = 200
            mock_stream.headers = {"content-length": "10240"}  # 10KB
            mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream.__aexit__ = AsyncMock(return_value=None)

            # aiter_bytes should never be called if Content-Length is checked
            mock_stream.aiter_bytes = MagicMock(
                side_effect=AssertionError("Should not stream")
            )

            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(SSRFFetchError, match="too large"):
                await ssrf_safe_fetch("https://example.com/api", max_size=5120)


class TestProxyMode:
    """Tests for FASTMCP_SSRF_TRUST_PROXY (proxy trust) mode.

    In proxy mode FastMCP skips its own DNS resolution and IP blocklist. Rather than
    predicting whether httpx2 would route a request through a proxy -- a strategy
    that broke three times chasing different NO_PROXY forms (port-qualified, IPv6,
    scheme-qualified) -- it reads the proxy URL directly from the environment and
    hands it to httpx2 explicitly with trust_env=False, so the request is provably
    routed through that proxy rather than predicted to be. NO_PROXY is therefore not
    evaluated in this mode. The scheme (HTTPS) and host checks still apply.
    """

    @pytest.fixture(autouse=True)
    def _clear_proxy_env(self, monkeypatch):
        """Start every test from a clean slate for both spellings of every proxy
        variable, so a proxy inherited from the host/CI environment (or left behind
        by another test) can't leak in and make behavior non-deterministic."""
        for name in (
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
            "NO_PROXY",
            "no_proxy",
        ):
            monkeypatch.delenv(name, raising=False)

    def test_flag_defaults_to_false(self):
        """The trust-proxy flag must be off by default (no silent weakening)."""
        assert fastmcp.settings.ssrf_trust_proxy is False

    async def test_validate_url_skips_resolution_and_blocklist(self, monkeypatch):
        """Proxy mode returns resolved_ips=[] without resolving or blocklisting, and
        carries the configured proxy URL for the fetch to use."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("fastmcp.server.auth.ssrf.resolve_hostname") as mock_resolve,
            patch("fastmcp.server.auth.ssrf.is_ip_allowed") as mock_blocklist,
        ):
            result = await validate_url("https://example.com/path")

        assert result.resolved_ips == []
        assert result.original_url == "https://example.com/path"
        assert result.hostname == "example.com"
        assert result.proxy_url == "http://proxy.internal:3128"
        mock_resolve.assert_not_called()
        mock_blocklist.assert_not_called()

    async def test_validate_url_still_rejects_http(self, monkeypatch):
        """Proxy mode keeps the HTTPS-only scheme check."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        with temporary_settings(ssrf_trust_proxy=True):
            with pytest.raises(SSRFError, match="must use HTTPS"):
                await validate_url("http://example.com/path")

    async def test_validate_url_still_rejects_missing_host(self, monkeypatch):
        """Proxy mode keeps the host check."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        with temporary_settings(ssrf_trust_proxy=True):
            with pytest.raises(SSRFError, match="must have a host"):
                await validate_url("https:///path")

    async def test_validate_url_still_enforces_require_path(self, monkeypatch):
        """Proxy mode keeps the require_path check (CIMD)."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        with temporary_settings(ssrf_trust_proxy=True):
            with pytest.raises(SSRFError, match="non-root path"):
                await validate_url("https://example.com/", require_path=True)

    async def test_raises_when_no_proxy_is_configured(self):
        """No proxy in the environment → refuse rather than fetch unprotected."""
        with temporary_settings(ssrf_trust_proxy=True):
            with pytest.raises(SSRFError, match="no HTTPS_PROXY/ALL_PROXY"):
                await validate_url("https://example.com/path")

    async def test_fetch_refuses_end_to_end_when_no_proxy_configured(self):
        """The refusal surfaces through ssrf_safe_fetch: no client is ever built.

        The whole point of the hard failure is that the *fetch* cannot proceed, so
        this drives it through the public entrypoint and asserts no httpx client is
        ever constructed — the request never leaves the process with the blocklist
        disabled.
        """
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("httpx2.AsyncClient") as mock_client_class,
        ):
            with pytest.raises(SSRFError, match="no HTTPS_PROXY/ALL_PROXY"):
                await ssrf_safe_fetch("https://example.com/api")

        mock_client_class.assert_not_called()

    async def test_https_proxy_used_explicitly(self, monkeypatch):
        """HTTPS_PROXY is passed to httpx2 explicitly with trust_env disabled, and a
        single request goes to the original hostname URL — not an IP literal.

        This is the property the whole redesign rests on: with an explicit proxy=
        and trust_env=False, httpx2 has no environment-based routing decision left
        to make differently than assumed.
        """
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        mock_client = _mock_httpx_client()
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("fastmcp.server.auth.ssrf.resolve_hostname") as mock_resolve,
            patch("httpx2.AsyncClient", return_value=mock_client) as mock_client_class,
        ):
            content = await ssrf_safe_fetch("https://example.com/api")

        assert content == b"ok"
        mock_resolve.assert_not_called()

        client_kwargs = mock_client_class.call_args[1]
        assert client_kwargs["proxy"] == "http://proxy.internal:3128"
        assert client_kwargs["trust_env"] is False

        # A single request to the original hostname URL — not an IP literal.
        assert mock_client.stream.call_count == 1
        url_called = mock_client.stream.call_args[0][1]
        assert url_called == "https://example.com/api"

        # No Host override and no SNI override — the client derives both from the URL.
        call_kwargs = mock_client.stream.call_args[1]
        assert "Host" not in call_kwargs["headers"]
        assert call_kwargs["extensions"] == {}

        # Redirects stay disabled and TLS verification stays on.
        assert client_kwargs["follow_redirects"] is False
        assert client_kwargs["verify"] is True

    async def test_all_proxy_used_as_fallback(self, monkeypatch):
        """ALL_PROXY routes the fetch when HTTPS_PROXY is not set."""
        monkeypatch.setenv("ALL_PROXY", "http://all-proxy.internal:3128")
        mock_client = _mock_httpx_client()
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("fastmcp.server.auth.ssrf.resolve_hostname"),
            patch("httpx2.AsyncClient", return_value=mock_client) as mock_client_class,
        ):
            content = await ssrf_safe_fetch("https://example.com/api")

        assert content == b"ok"
        client_kwargs = mock_client_class.call_args[1]
        assert client_kwargs["proxy"] == "http://all-proxy.internal:3128"
        assert client_kwargs["trust_env"] is False

    async def test_https_proxy_preferred_over_all_proxy(self, monkeypatch):
        """When both are set, HTTPS_PROXY takes priority."""
        monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy.internal:3128")
        monkeypatch.setenv("ALL_PROXY", "http://all-proxy.internal:3128")
        mock_client = _mock_httpx_client()
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("fastmcp.server.auth.ssrf.resolve_hostname"),
            patch("httpx2.AsyncClient", return_value=mock_client) as mock_client_class,
        ):
            await ssrf_safe_fetch("https://example.com/api")

        proxy_used = mock_client_class.call_args[1]["proxy"]
        assert proxy_used == "http://https-proxy.internal:3128"

    async def test_no_proxy_is_not_honored(self, monkeypatch):
        """Documents the behavior change: a NO_PROXY entry that would previously have
        matched the target host no longer excludes it. The fetch still proceeds
        through the configured proxy rather than being refused, because routing a
        NO_PROXY'd host through the proxy is strictly safer than the alternative —
        fetching it direct with the IP blocklist already disabled.
        """
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        monkeypatch.setenv("NO_PROXY", "example.com")
        mock_client = _mock_httpx_client()
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("fastmcp.server.auth.ssrf.resolve_hostname"),
            patch("httpx2.AsyncClient", return_value=mock_client) as mock_client_class,
        ):
            content = await ssrf_safe_fetch("https://example.com/api")

        assert content == b"ok"
        client_kwargs = mock_client_class.call_args[1]
        assert client_kwargs["proxy"] == "http://proxy.internal:3128"
        assert client_kwargs["trust_env"] is False

    async def test_fetch_preserves_request_headers_but_drops_host(self, monkeypatch):
        """Caller headers pass through, but a caller-supplied Host is dropped."""
        from fastmcp.server.auth.ssrf import ssrf_safe_fetch_response

        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        mock_client = _mock_httpx_client()
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("fastmcp.server.auth.ssrf.resolve_hostname"),
            patch("httpx2.AsyncClient", return_value=mock_client),
        ):
            await ssrf_safe_fetch_response(
                "https://example.com/api",
                request_headers={"If-None-Match": "etag", "Host": "evil.example"},
            )

        sent_headers = mock_client.stream.call_args[1]["headers"]
        assert sent_headers["If-None-Match"] == "etag"
        assert "Host" not in sent_headers

    async def test_fetch_size_limit_preserved(self, monkeypatch):
        """Proxy mode still enforces the response size limit during streaming."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        big_chunks = [b"x" * 1024 for _ in range(10)]
        mock_client = _mock_httpx_client(headers={}, body_chunks=big_chunks)
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("fastmcp.server.auth.ssrf.resolve_hostname"),
            patch("httpx2.AsyncClient", return_value=mock_client),
        ):
            with pytest.raises(SSRFFetchError, match="too large"):
                await ssrf_safe_fetch("https://example.com/api", max_size=5120)

    async def test_fetch_status_check_preserved(self, monkeypatch):
        """Proxy mode still rejects non-allowed status codes."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        mock_client = _mock_httpx_client(status_code=404, body_chunks=[b"no"])
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("fastmcp.server.auth.ssrf.resolve_hostname"),
            patch("httpx2.AsyncClient", return_value=mock_client),
        ):
            with pytest.raises(SSRFFetchError, match="HTTP 404"):
                await ssrf_safe_fetch("https://example.com/api")

    async def test_gaierror_repro_succeeds_through_proxy(self, monkeypatch):
        """Reproduces issue #4292: on a host with no external DNS at all (every
        getaddrinfo() call raises gaierror), the OAuth/JWKS fetch still succeeds in
        proxy-trust mode, because DNS resolution is never attempted — only HTTPS_PROXY
        is read and the proxy resolves the target. This is the reporter's exact
        failure mode, and the strongest proof the redesign closes the issue: unlike
        other tests in this class, resolve_hostname itself is *not* mocked, so if
        proxy mode ever regressed into calling it, this test would fail with SSRFError
        instead of succeeding.
        """
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")

        def _no_dns(*args, **kwargs):
            raise socket.gaierror("Name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", _no_dns)

        mock_client = _mock_httpx_client()
        with (
            temporary_settings(ssrf_trust_proxy=True),
            patch("httpx2.AsyncClient", return_value=mock_client) as mock_client_class,
        ):
            content = await ssrf_safe_fetch("https://example.com/api")

        assert content == b"ok"
        client_kwargs = mock_client_class.call_args[1]
        assert client_kwargs["proxy"] == "http://proxy.internal:3128"
        assert client_kwargs["trust_env"] is False
        assert mock_client.stream.call_args[0][1] == "https://example.com/api"

    async def test_default_mode_still_resolves_and_pins(self):
        """Regression: with the flag off, resolution + blocklist + IP pinning still
        apply, and no explicit proxy is passed to the client."""
        resolved_ip = "93.184.216.34"
        mock_client = _mock_httpx_client()
        with (
            patch(
                "fastmcp.server.auth.ssrf.resolve_hostname",
                return_value=[resolved_ip],
            ) as mock_resolve,
            patch("httpx2.AsyncClient", return_value=mock_client) as mock_client_class,
        ):
            assert fastmcp.settings.ssrf_trust_proxy is False
            await ssrf_safe_fetch("https://example.com/api")

        mock_resolve.assert_called_once()

        # Connection is pinned to the resolved IP literal, with Host + SNI = hostname.
        call_args = mock_client.stream.call_args
        url_called = call_args[0][1]
        assert resolved_ip in url_called
        assert call_args[1]["headers"]["Host"] == "example.com"
        assert call_args[1]["extensions"] == {"sni_hostname": "example.com"}

        # No explicit proxy is passed, and trust_env keeps its normal default.
        client_kwargs = mock_client_class.call_args[1]
        assert client_kwargs["proxy"] is None
        assert client_kwargs["trust_env"] is True

    async def test_default_mode_ignores_proxy_env_vars(self, monkeypatch):
        """Regression: proxy env vars — including a hostile NO_PROXY that previously
        caused non-deterministic failures — must not affect the default (non-trust)
        path at all, since it never reads them."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
        monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1")
        resolved_ip = "93.184.216.34"
        mock_client = _mock_httpx_client()
        with (
            patch(
                "fastmcp.server.auth.ssrf.resolve_hostname",
                return_value=[resolved_ip],
            ) as mock_resolve,
            patch("httpx2.AsyncClient", return_value=mock_client) as mock_client_class,
        ):
            assert fastmcp.settings.ssrf_trust_proxy is False
            await ssrf_safe_fetch("https://example.com/api")

        mock_resolve.assert_called_once()
        assert mock_client_class.call_args[1]["proxy"] is None
        assert mock_client_class.call_args[1]["trust_env"] is True
