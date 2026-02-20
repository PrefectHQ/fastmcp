"""Tests for SSRF-safe HTTP utilities.

This module tests the ssrf.py module which provides SSRF-protected HTTP fetching.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from fastmcp.server.auth.ssrf import (
    SSRFError,
    SSRFFetchError,
    is_ip_allowed,
    ssrf_safe_fetch,
    validate_url,
)


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
            patch("httpx.AsyncClient") as mock_client_class,
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
            patch("httpx.AsyncClient") as mock_client_class,
        ):
            request = httpx.Request("GET", "https://example.com/api")

            first_client = AsyncMock()
            first_client.stream = MagicMock(
                side_effect=httpx.RequestError("boom", request=request)
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
            patch("httpx.AsyncClient") as mock_client_class,
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
            patch("httpx.AsyncClient") as mock_client_class,
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
            patch("httpx.AsyncClient") as mock_client_class,
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
            patch("httpx.AsyncClient") as mock_client_class,
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
            patch("httpx.AsyncClient") as mock_client_class,
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
