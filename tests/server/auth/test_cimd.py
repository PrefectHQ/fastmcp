"""Unit tests for CIMD (Client ID Metadata Document) functionality."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from pydantic import AnyHttpUrl, ValidationError

from fastmcp.server.auth.cimd import (
    CIMDDocument,
    CIMDFetcher,
    CIMDFetchError,
    CIMDValidationError,
)

# Standard public IP used for DNS mocking in tests
TEST_PUBLIC_IP = "93.184.216.34"


class TestCIMDDocument:
    """Tests for CIMDDocument model validation."""

    def test_valid_minimal_document(self):
        """Test that minimal valid document passes validation."""
        doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:3000/callback"],
        )
        assert str(doc.client_id) == "https://example.com/client.json"
        assert doc.token_endpoint_auth_method == "none"
        assert doc.grant_types == ["authorization_code"]
        assert doc.response_types == ["code"]

    def test_valid_full_document(self):
        """Test that full document passes validation."""
        doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            client_name="My App",
            client_uri=AnyHttpUrl("https://example.com"),
            logo_uri=AnyHttpUrl("https://example.com/logo.png"),
            redirect_uris=["http://localhost:3000/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="read write",
        )
        assert doc.client_name == "My App"
        assert doc.scope == "read write"

    def test_private_key_jwt_auth_method_allowed(self):
        """Test that private_key_jwt is allowed for CIMD."""
        doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:3000/callback"],
            token_endpoint_auth_method="private_key_jwt",
            jwks_uri=AnyHttpUrl("https://example.com/.well-known/jwks.json"),
        )
        assert doc.token_endpoint_auth_method == "private_key_jwt"

    def test_client_secret_basic_rejected(self):
        """Test that client_secret_basic is rejected for CIMD."""
        with pytest.raises(ValidationError) as exc_info:
            CIMDDocument(
                client_id=AnyHttpUrl("https://example.com/client.json"),
                redirect_uris=["http://localhost:3000/callback"],
                token_endpoint_auth_method="client_secret_basic",  # type: ignore[arg-type] - testing invalid value
            )
        # Literal type rejects invalid values before custom validator
        assert "token_endpoint_auth_method" in str(exc_info.value)

    def test_client_secret_post_rejected(self):
        """Test that client_secret_post is rejected for CIMD."""
        with pytest.raises(ValidationError) as exc_info:
            CIMDDocument(
                client_id=AnyHttpUrl("https://example.com/client.json"),
                redirect_uris=["http://localhost:3000/callback"],
                token_endpoint_auth_method="client_secret_post",  # type: ignore[arg-type] - testing invalid value
            )
        assert "token_endpoint_auth_method" in str(exc_info.value)

    def test_client_secret_jwt_rejected(self):
        """Test that client_secret_jwt is rejected for CIMD."""
        with pytest.raises(ValidationError) as exc_info:
            CIMDDocument(
                client_id=AnyHttpUrl("https://example.com/client.json"),
                redirect_uris=["http://localhost:3000/callback"],
                token_endpoint_auth_method="client_secret_jwt",  # type: ignore[arg-type] - testing invalid value
            )
        assert "token_endpoint_auth_method" in str(exc_info.value)

    def test_missing_redirect_uris_rejected(self):
        """Test that redirect_uris is required for CIMD."""
        with pytest.raises(ValidationError) as exc_info:
            CIMDDocument(client_id=AnyHttpUrl("https://example.com/client.json"))
        assert "redirect_uris" in str(exc_info.value)

    def test_empty_redirect_uris_rejected(self):
        """Test that empty redirect_uris is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CIMDDocument(
                client_id=AnyHttpUrl("https://example.com/client.json"),
                redirect_uris=[],
            )
        assert "redirect_uris" in str(exc_info.value)

    def test_redirect_uri_without_scheme_rejected(self):
        """Test that redirect_uris without a scheme are rejected."""
        with pytest.raises(ValidationError, match="must have a scheme"):
            CIMDDocument(
                client_id=AnyHttpUrl("https://example.com/client.json"),
                redirect_uris=["/just/a/path"],
            )

    def test_redirect_uri_without_host_rejected(self):
        """Test that redirect_uris without a host are rejected."""
        with pytest.raises(ValidationError, match="must have a host"):
            CIMDDocument(
                client_id=AnyHttpUrl("https://example.com/client.json"),
                redirect_uris=["http://"],
            )

    def test_redirect_uri_whitespace_only_rejected(self):
        """Test that whitespace-only redirect_uris are rejected."""
        with pytest.raises(ValidationError, match="non-empty"):
            CIMDDocument(
                client_id=AnyHttpUrl("https://example.com/client.json"),
                redirect_uris=["   "],
            )


class TestCIMDFetcher:
    """Tests for CIMDFetcher."""

    @pytest.fixture
    def fetcher(self):
        """Create a CIMDFetcher for testing."""
        return CIMDFetcher()

    def test_is_cimd_client_id_valid_urls(self, fetcher: CIMDFetcher):
        """Test is_cimd_client_id accepts valid CIMD URLs."""
        assert fetcher.is_cimd_client_id("https://example.com/client.json")
        assert fetcher.is_cimd_client_id("https://example.com/path/to/client")
        assert fetcher.is_cimd_client_id("https://sub.example.com/cimd.json")

    def test_is_cimd_client_id_rejects_http(self, fetcher: CIMDFetcher):
        """Test is_cimd_client_id rejects HTTP URLs."""
        assert not fetcher.is_cimd_client_id("http://example.com/client.json")

    def test_is_cimd_client_id_rejects_root_path(self, fetcher: CIMDFetcher):
        """Test is_cimd_client_id rejects URLs with no path."""
        assert not fetcher.is_cimd_client_id("https://example.com/")
        assert not fetcher.is_cimd_client_id("https://example.com")

    def test_is_cimd_client_id_rejects_non_url(self, fetcher: CIMDFetcher):
        """Test is_cimd_client_id rejects non-URL strings."""
        assert not fetcher.is_cimd_client_id("client-123")
        assert not fetcher.is_cimd_client_id("my-client")
        assert not fetcher.is_cimd_client_id("")
        assert not fetcher.is_cimd_client_id("not a url")

    def test_validate_redirect_uri_exact_match(self, fetcher: CIMDFetcher):
        """Test redirect_uri validation with exact match."""
        doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:3000/callback"],
        )
        assert fetcher.validate_redirect_uri(doc, "http://localhost:3000/callback")
        assert not fetcher.validate_redirect_uri(doc, "http://localhost:4000/callback")

    def test_validate_redirect_uri_wildcard_match(self, fetcher: CIMDFetcher):
        """Test redirect_uri validation with wildcard port."""
        doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:*/callback"],
        )
        assert fetcher.validate_redirect_uri(doc, "http://localhost:3000/callback")
        assert fetcher.validate_redirect_uri(doc, "http://localhost:8080/callback")
        assert not fetcher.validate_redirect_uri(doc, "http://localhost:3000/other")


class TestCIMDFetcherHTTP:
    """Tests for CIMDFetcher HTTP fetching (using httpx mock).

    Note: With SSRF protection and DNS pinning, HTTP requests go to the resolved IP
    instead of the hostname. These tests mock DNS resolution to return a public IP
    and configure httpx_mock to expect the IP-based URL.
    """

    @pytest.fixture
    def fetcher(self):
        """Create a CIMDFetcher for testing."""
        return CIMDFetcher()

    @pytest.fixture
    def mock_dns(self):
        """Mock DNS resolution to return test public IP."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=[TEST_PUBLIC_IP],
        ):
            yield

    async def test_fetch_success(self, fetcher: CIMDFetcher, httpx_mock, mock_dns):
        """Test successful CIMD document fetch."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Test App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }

        # With DNS pinning, request goes to IP. Match any URL.
        httpx_mock.add_response(
            json=doc_data,
            headers={
                "content-type": "application/json",
                "content-length": "200",
            },
        )

        doc = await fetcher.fetch(url)
        assert str(doc.client_id) == url
        assert doc.client_name == "Test App"

    async def test_fetch_ttl_cache(self, fetcher: CIMDFetcher, httpx_mock, mock_dns):
        """Test that fetched documents are cached and served from cache within TTL."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Test App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "200"},
        )

        first = await fetcher.fetch(url)
        second = await fetcher.fetch(url)

        assert first.client_id == second.client_id
        assert len(httpx_mock.get_requests()) == 1

    async def test_fetch_cache_control_max_age(
        self, fetcher: CIMDFetcher, httpx_mock, mock_dns
    ):
        """Cache-Control max-age should prevent refetch before expiry."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Max-Age App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"cache-control": "max-age=60", "content-length": "200"},
        )

        first = await fetcher.fetch(url)
        second = await fetcher.fetch(url)

        assert first.client_name == second.client_name
        assert len(httpx_mock.get_requests()) == 1

    async def test_fetch_etag_revalidation_304(
        self, fetcher: CIMDFetcher, httpx_mock, mock_dns
    ):
        """Expired cache should revalidate with ETag and accept 304."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "ETag App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={
                "cache-control": "max-age=0",
                "etag": '"v1"',
                "content-length": "200",
            },
        )
        httpx_mock.add_response(
            status_code=304,
            headers={
                "cache-control": "max-age=120",
                "etag": '"v1"',
                "content-length": "0",
            },
        )

        first = await fetcher.fetch(url)
        second = await fetcher.fetch(url)
        requests = httpx_mock.get_requests()

        assert first.client_name == "ETag App"
        assert second.client_name == "ETag App"
        assert len(requests) == 2
        assert requests[1].headers.get("if-none-match") == '"v1"'

    async def test_fetch_last_modified_revalidation_304(
        self, fetcher: CIMDFetcher, httpx_mock, mock_dns
    ):
        """Expired cache should revalidate with Last-Modified and accept 304."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Last-Modified App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        last_modified = "Wed, 21 Oct 2015 07:28:00 GMT"
        httpx_mock.add_response(
            json=doc_data,
            headers={
                "cache-control": "max-age=0",
                "last-modified": last_modified,
                "content-length": "200",
            },
        )
        httpx_mock.add_response(
            status_code=304,
            headers={"cache-control": "max-age=120", "content-length": "0"},
        )

        first = await fetcher.fetch(url)
        second = await fetcher.fetch(url)
        requests = httpx_mock.get_requests()

        assert first.client_name == "Last-Modified App"
        assert second.client_name == "Last-Modified App"
        assert len(requests) == 2
        assert requests[1].headers.get("if-modified-since") == last_modified

    async def test_fetch_cache_control_no_store(
        self, fetcher: CIMDFetcher, httpx_mock, mock_dns
    ):
        """Cache-Control no-store should prevent storing CIMD documents."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "No-Store App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"cache-control": "no-store", "content-length": "200"},
        )
        httpx_mock.add_response(
            json=doc_data,
            headers={"cache-control": "no-store", "content-length": "200"},
        )

        first = await fetcher.fetch(url)
        second = await fetcher.fetch(url)

        assert first.client_name == second.client_name
        assert len(httpx_mock.get_requests()) == 2

    async def test_fetch_cache_control_no_cache(
        self, fetcher: CIMDFetcher, httpx_mock, mock_dns
    ):
        """Cache-Control no-cache should force revalidation on each fetch."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "No-Cache App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={
                "cache-control": "no-cache",
                "etag": '"v2"',
                "content-length": "200",
            },
        )
        httpx_mock.add_response(
            status_code=304,
            headers={
                "cache-control": "no-cache",
                "etag": '"v2"',
                "content-length": "0",
            },
        )

        first = await fetcher.fetch(url)
        second = await fetcher.fetch(url)
        requests = httpx_mock.get_requests()

        assert first.client_name == "No-Cache App"
        assert second.client_name == "No-Cache App"
        assert len(requests) == 2
        assert requests[1].headers.get("if-none-match") == '"v2"'

    async def test_fetch_304_without_cache_headers_preserves_policy(
        self, fetcher: CIMDFetcher, httpx_mock, mock_dns
    ):
        """304 responses without cache headers should not reset cached policy."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "No-Header-304 App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={
                "cache-control": "no-cache",
                "etag": '"v3"',
                "content-length": "200",
            },
        )
        # Intentionally omit cache-control/expires on 304.
        httpx_mock.add_response(
            status_code=304,
            headers={"content-length": "0"},
        )
        httpx_mock.add_response(
            status_code=304,
            headers={"content-length": "0"},
        )

        first = await fetcher.fetch(url)
        second = await fetcher.fetch(url)
        third = await fetcher.fetch(url)
        requests = httpx_mock.get_requests()

        assert first.client_name == "No-Header-304 App"
        assert second.client_name == "No-Header-304 App"
        assert third.client_name == "No-Header-304 App"
        assert len(requests) == 3
        assert requests[1].headers.get("if-none-match") == '"v3"'
        assert requests[2].headers.get("if-none-match") == '"v3"'

    async def test_fetch_304_without_cache_headers_refreshes_cached_freshness(
        self, fetcher: CIMDFetcher, httpx_mock, mock_dns
    ):
        """A header-less 304 should renew freshness using cached lifetime."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Headerless 304 Freshness App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={
                "cache-control": "max-age=60",
                "etag": '"v4"',
                "content-length": "200",
            },
        )
        httpx_mock.add_response(
            status_code=304,
            headers={"content-length": "0"},
        )

        first = await fetcher.fetch(url)

        # Simulate cache expiry so the next request triggers revalidation.
        cached_entry = fetcher._cache[url]
        cached_entry.expires_at = time.time() - 1

        second = await fetcher.fetch(url)
        third = await fetcher.fetch(url)
        requests = httpx_mock.get_requests()

        assert first.client_name == "Headerless 304 Freshness App"
        assert second.client_name == "Headerless 304 Freshness App"
        assert third.client_name == "Headerless 304 Freshness App"
        assert len(requests) == 2
        assert requests[1].headers.get("if-none-match") == '"v4"'

    async def test_fetch_client_id_mismatch(
        self, fetcher: CIMDFetcher, httpx_mock, mock_dns
    ):
        """Test that client_id mismatch is rejected."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": "https://other.com/client.json",  # Different URL
            "client_name": "Test App",
            "redirect_uris": ["http://localhost:3000/callback"],
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "100"},
        )

        with pytest.raises(CIMDValidationError) as exc_info:
            await fetcher.fetch(url)
        assert "mismatch" in str(exc_info.value).lower()

    async def test_fetch_http_error(self, fetcher: CIMDFetcher, httpx_mock, mock_dns):
        """Test handling of HTTP errors."""
        url = "https://example.com/client.json"
        httpx_mock.add_response(status_code=404)

        with pytest.raises(CIMDFetchError) as exc_info:
            await fetcher.fetch(url)
        assert "404" in str(exc_info.value)

    async def test_fetch_invalid_json(self, fetcher: CIMDFetcher, httpx_mock, mock_dns):
        """Test handling of invalid JSON response."""
        url = "https://example.com/client.json"
        httpx_mock.add_response(
            content=b"not json",
            headers={"content-length": "10"},
        )

        with pytest.raises(CIMDValidationError) as exc_info:
            await fetcher.fetch(url)
        assert "JSON" in str(exc_info.value)

    async def test_fetch_invalid_document(
        self, fetcher: CIMDFetcher, httpx_mock, mock_dns
    ):
        """Test handling of invalid CIMD document."""
        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "client_secret_basic",  # Not allowed
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "100"},
        )

        with pytest.raises(CIMDValidationError) as exc_info:
            await fetcher.fetch(url)
        assert "Invalid CIMD document" in str(exc_info.value)
