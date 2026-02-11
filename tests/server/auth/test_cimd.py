"""Unit tests for CIMD (Client ID Metadata Document) functionality."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import AnyHttpUrl, ValidationError

from fastmcp.server.auth.cimd import (
    CIMDAssertionValidator,
    CIMDClientManager,
    CIMDDocument,
    CIMDFetcher,
    CIMDFetchError,
    CIMDValidationError,
)
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient

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


class TestCIMDAssertionValidator:
    """Tests for CIMDAssertionValidator (private_key_jwt support)."""

    @pytest.fixture
    def validator(self):
        """Create a CIMDAssertionValidator for testing."""
        return CIMDAssertionValidator()

    @pytest.fixture
    def key_pair(self):
        """Generate RSA key pair for testing."""
        from fastmcp.server.auth.providers.jwt import RSAKeyPair

        return RSAKeyPair.generate()

    @pytest.fixture
    def jwks(self, key_pair):
        """Create JWKS from key pair."""
        import base64

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        # Load public key
        public_key = serialization.load_pem_public_key(
            key_pair.public_key.encode(), backend=default_backend()
        )

        # Get RSA public numbers
        from cryptography.hazmat.primitives.asymmetric import rsa

        if isinstance(public_key, rsa.RSAPublicKey):
            numbers = public_key.public_numbers()

            # Convert to JWK format
            return {
                "keys": [
                    {
                        "kty": "RSA",
                        "kid": "test-key-1",
                        "use": "sig",
                        "alg": "RS256",
                        "n": base64.urlsafe_b64encode(
                            numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
                        )
                        .rstrip(b"=")
                        .decode(),
                        "e": base64.urlsafe_b64encode(
                            numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
                        )
                        .rstrip(b"=")
                        .decode(),
                    }
                ]
            }

    @pytest.fixture
    def cimd_doc_with_jwks_uri(self):
        """Create CIMD document with jwks_uri."""
        return CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:3000/callback"],
            token_endpoint_auth_method="private_key_jwt",
            jwks_uri=AnyHttpUrl("https://example.com/.well-known/jwks.json"),
        )

    @pytest.fixture
    def cimd_doc_with_inline_jwks(self, jwks):
        """Create CIMD document with inline JWKS."""
        return CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:3000/callback"],
            token_endpoint_auth_method="private_key_jwt",
            jwks=jwks,
        )

    async def test_valid_assertion_with_jwks_uri(
        self, validator, key_pair, cimd_doc_with_jwks_uri, httpx_mock
    ):
        """Test that valid JWT assertion passes validation (jwks_uri)."""
        client_id = "https://example.com/client.json"
        token_endpoint = "https://oauth.example.com/token"

        # Mock JWKS endpoint
        import base64

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        public_key = serialization.load_pem_public_key(
            key_pair.public_key.encode(), backend=default_backend()
        )
        from cryptography.hazmat.primitives.asymmetric import rsa

        assert isinstance(public_key, rsa.RSAPublicKey)
        numbers = public_key.public_numbers()

        jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "test-key-1",
                    "use": "sig",
                    "alg": "RS256",
                    "n": base64.urlsafe_b64encode(
                        numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
                    )
                    .rstrip(b"=")
                    .decode(),
                    "e": base64.urlsafe_b64encode(
                        numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
                    )
                    .rstrip(b"=")
                    .decode(),
                }
            ]
        }

        # Mock DNS resolution for SSRF-safe fetch
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=[TEST_PUBLIC_IP],
        ):
            httpx_mock.add_response(json=jwks)

            # Create valid assertion (use short lifetime for security compliance)
            assertion = key_pair.create_token(
                subject=client_id,
                issuer=client_id,
                audience=token_endpoint,
                additional_claims={"jti": "unique-jti-123"},
                expires_in_seconds=60,  # 1 minute (max allowed is 300s)
                kid="test-key-1",
            )

            # Should validate successfully
            assert await validator.validate_assertion(
                assertion, client_id, token_endpoint, cimd_doc_with_jwks_uri
            )

    async def test_valid_assertion_with_inline_jwks(
        self, validator, key_pair, cimd_doc_with_inline_jwks
    ):
        """Test that valid JWT assertion passes validation (inline JWKS)."""
        client_id = "https://example.com/client.json"
        token_endpoint = "https://oauth.example.com/token"

        # Create valid assertion (use short lifetime for security compliance)
        assertion = key_pair.create_token(
            subject=client_id,
            issuer=client_id,
            audience=token_endpoint,
            additional_claims={"jti": "unique-jti-456"},
            expires_in_seconds=60,  # 1 minute (max allowed is 300s)
            kid="test-key-1",
        )

        # Should validate successfully
        assert await validator.validate_assertion(
            assertion, client_id, token_endpoint, cimd_doc_with_inline_jwks
        )

    async def test_rejects_wrong_issuer(
        self, validator, key_pair, cimd_doc_with_inline_jwks
    ):
        """Test that wrong issuer is rejected."""
        client_id = "https://example.com/client.json"
        token_endpoint = "https://oauth.example.com/token"

        # Create assertion with wrong issuer
        assertion = key_pair.create_token(
            subject=client_id,
            issuer="https://attacker.com",  # Wrong!
            audience=token_endpoint,
            additional_claims={"jti": "unique-jti-789"},
            expires_in_seconds=60,
            kid="test-key-1",
        )

        with pytest.raises(ValueError) as exc_info:
            await validator.validate_assertion(
                assertion, client_id, token_endpoint, cimd_doc_with_inline_jwks
            )
        assert "Invalid JWT assertion" in str(exc_info.value)

    async def test_rejects_wrong_audience(
        self, validator, key_pair, cimd_doc_with_inline_jwks
    ):
        """Test that wrong audience is rejected."""
        client_id = "https://example.com/client.json"
        token_endpoint = "https://oauth.example.com/token"

        # Create assertion with wrong audience
        assertion = key_pair.create_token(
            subject=client_id,
            issuer=client_id,
            audience="https://wrong-endpoint.com/token",  # Wrong!
            additional_claims={"jti": "unique-jti-abc"},
            expires_in_seconds=60,
            kid="test-key-1",
        )

        with pytest.raises(ValueError) as exc_info:
            await validator.validate_assertion(
                assertion, client_id, token_endpoint, cimd_doc_with_inline_jwks
            )
        assert "Invalid JWT assertion" in str(exc_info.value)

    async def test_rejects_wrong_subject(
        self, validator, key_pair, cimd_doc_with_inline_jwks
    ):
        """Test that wrong subject claim is rejected."""
        client_id = "https://example.com/client.json"
        token_endpoint = "https://oauth.example.com/token"

        # Create assertion with wrong subject
        assertion = key_pair.create_token(
            subject="https://different-client.com",  # Wrong!
            issuer=client_id,
            audience=token_endpoint,
            additional_claims={"jti": "unique-jti-def"},
            expires_in_seconds=60,
            kid="test-key-1",
        )

        with pytest.raises(ValueError) as exc_info:
            await validator.validate_assertion(
                assertion, client_id, token_endpoint, cimd_doc_with_inline_jwks
            )
        assert "sub claim must be" in str(exc_info.value)

    async def test_rejects_missing_jti(
        self, validator, key_pair, cimd_doc_with_inline_jwks
    ):
        """Test that missing jti claim is rejected."""
        client_id = "https://example.com/client.json"
        token_endpoint = "https://oauth.example.com/token"

        # Create assertion without jti
        assertion = key_pair.create_token(
            subject=client_id,
            issuer=client_id,
            audience=token_endpoint,
            # No jti!
            expires_in_seconds=60,
            kid="test-key-1",
        )

        with pytest.raises(ValueError) as exc_info:
            await validator.validate_assertion(
                assertion, client_id, token_endpoint, cimd_doc_with_inline_jwks
            )
        assert "jti claim" in str(exc_info.value)

    async def test_rejects_replayed_jti(
        self, validator, key_pair, cimd_doc_with_inline_jwks
    ):
        """Test that replayed JTI is detected and rejected."""
        client_id = "https://example.com/client.json"
        token_endpoint = "https://oauth.example.com/token"

        # Create assertion
        assertion = key_pair.create_token(
            subject=client_id,
            issuer=client_id,
            audience=token_endpoint,
            additional_claims={"jti": "replayed-jti"},
            expires_in_seconds=60,
            kid="test-key-1",
        )

        # First use should succeed
        assert await validator.validate_assertion(
            assertion, client_id, token_endpoint, cimd_doc_with_inline_jwks
        )

        # Second use with same jti should fail (replay attack)
        with pytest.raises(ValueError) as exc_info:
            await validator.validate_assertion(
                assertion, client_id, token_endpoint, cimd_doc_with_inline_jwks
            )
        assert "replay" in str(exc_info.value).lower()

    async def test_rejects_expired_token(
        self, validator, key_pair, cimd_doc_with_inline_jwks
    ):
        """Test that expired tokens are rejected."""
        client_id = "https://example.com/client.json"
        token_endpoint = "https://oauth.example.com/token"

        # Create expired assertion (expired 1 hour ago)
        assertion = key_pair.create_token(
            subject=client_id,
            issuer=client_id,
            audience=token_endpoint,
            additional_claims={"jti": "expired-jti"},
            expires_in_seconds=-3600,  # Negative = expired
            kid="test-key-1",
        )

        with pytest.raises(ValueError) as exc_info:
            await validator.validate_assertion(
                assertion, client_id, token_endpoint, cimd_doc_with_inline_jwks
            )
        assert "Invalid JWT assertion" in str(exc_info.value)


class TestCIMDClientManager:
    """Tests for CIMDClientManager."""

    @pytest.fixture
    def manager(self):
        """Create a CIMDClientManager for testing."""
        return CIMDClientManager(enable_cimd=True)

    @pytest.fixture
    def disabled_manager(self):
        """Create a disabled CIMDClientManager for testing."""
        return CIMDClientManager(enable_cimd=False)

    @pytest.fixture
    def mock_dns(self):
        """Mock DNS resolution to return test public IP."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=[TEST_PUBLIC_IP],
        ):
            yield

    def test_is_cimd_client_id_enabled(self, manager):
        """Test CIMD URL detection when enabled."""
        assert manager.is_cimd_client_id("https://example.com/client.json")
        assert not manager.is_cimd_client_id("regular-client-id")

    def test_is_cimd_client_id_disabled(self, disabled_manager):
        """Test CIMD URL detection when disabled."""
        assert not disabled_manager.is_cimd_client_id("https://example.com/client.json")
        assert not disabled_manager.is_cimd_client_id("regular-client-id")

    async def test_get_client_success(self, manager, httpx_mock, mock_dns):
        """Test successful CIMD client creation."""
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

        client = await manager.get_client(url)
        assert client is not None
        assert client.client_id == url
        assert client.client_name == "Test App"
        # Verify it uses proxy's patterns (None by default), not document's redirect_uris
        assert client.allowed_redirect_uri_patterns is None

    async def test_get_client_disabled(self, disabled_manager):
        """Test that get_client returns None when disabled."""
        client = await disabled_manager.get_client("https://example.com/client.json")
        assert client is None

    async def test_get_client_fetch_failure(self, manager, httpx_mock, mock_dns):
        """Test that get_client returns None on fetch failure."""
        url = "https://example.com/client.json"
        httpx_mock.add_response(status_code=404)

        client = await manager.get_client(url)
        assert client is None

    # Trust policy and consent bypass tests removed - functionality removed from CIMD


class TestCIMDClientManagerGetClientOptions:
    """Tests for CIMDClientManager.get_client with default_scope and allowed patterns."""

    @pytest.fixture
    def mock_dns(self):
        """Mock DNS resolution to return test public IP."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=[TEST_PUBLIC_IP],
        ):
            yield

    async def test_default_scope_applied_when_doc_has_no_scope(
        self, httpx_mock, mock_dns
    ):
        """When the CIMD document omits scope, the manager's default_scope is used."""

        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Test App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
            # No scope field
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "200"},
        )

        manager = CIMDClientManager(
            enable_cimd=True,
            default_scope="read write admin",
        )
        client = await manager.get_client(url)
        assert client is not None
        assert client.scope == "read write admin"

    async def test_doc_scope_takes_precedence_over_default(self, httpx_mock, mock_dns):
        """When the CIMD document specifies scope, it wins over the default."""

        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Test App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
            "scope": "custom-scope",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "200"},
        )

        manager = CIMDClientManager(
            enable_cimd=True,
            default_scope="default-scope",
        )
        client = await manager.get_client(url)
        assert client is not None
        assert client.scope == "custom-scope"

    async def test_allowed_redirect_uri_patterns_stored_on_client(
        self, httpx_mock, mock_dns
    ):
        """Proxy's allowed_redirect_uri_patterns are forwarded to the created client."""

        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Test App",
            "redirect_uris": ["http://localhost:*/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "200"},
        )

        patterns = ["http://localhost:*", "https://app.example.com/*"]
        manager = CIMDClientManager(
            enable_cimd=True,
            allowed_redirect_uri_patterns=patterns,
        )
        client = await manager.get_client(url)
        assert client is not None
        assert client.allowed_redirect_uri_patterns == patterns

    async def test_cimd_document_attached_to_client(self, httpx_mock, mock_dns):
        """The fetched CIMDDocument is attached to the created client."""

        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Attached Doc App",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "200"},
        )

        manager = CIMDClientManager(enable_cimd=True)
        client = await manager.get_client(url)
        assert client is not None
        assert client.cimd_document is not None
        assert client.cimd_document.client_name == "Attached Doc App"
        assert str(client.cimd_document.client_id) == url


class TestCIMDClientManagerValidatePrivateKeyJwt:
    """Tests for CIMDClientManager.validate_private_key_jwt wrapper."""

    @pytest.fixture
    def manager(self):
        return CIMDClientManager(enable_cimd=True)

    async def test_missing_cimd_document_raises(self, manager):
        """validate_private_key_jwt raises ValueError if client has no cimd_document."""

        client = ProxyDCRClient(
            client_id="https://example.com/client.json",
            client_secret=None,
            redirect_uris=None,
            cimd_document=None,
        )
        with pytest.raises(ValueError, match="must have CIMD document"):
            await manager.validate_private_key_jwt(
                "fake.jwt.token",
                client,
                "https://oauth.example.com/token",
            )

    async def test_wrong_auth_method_raises(self, manager):
        """validate_private_key_jwt raises ValueError if auth method is not private_key_jwt."""

        cimd_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:3000/callback"],
            token_endpoint_auth_method="none",  # Not private_key_jwt
        )
        client = ProxyDCRClient(
            client_id="https://example.com/client.json",
            client_secret=None,
            redirect_uris=None,
            cimd_document=cimd_doc,
        )
        with pytest.raises(ValueError, match="private_key_jwt"):
            await manager.validate_private_key_jwt(
                "fake.jwt.token",
                client,
                "https://oauth.example.com/token",
            )

    async def test_success_delegates_to_assertion_validator(self, manager):
        """On success, validate_private_key_jwt delegates to the assertion validator."""

        cimd_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/client.json"),
            redirect_uris=["http://localhost:3000/callback"],
            token_endpoint_auth_method="private_key_jwt",
            jwks_uri=AnyHttpUrl("https://example.com/.well-known/jwks.json"),
        )
        client = ProxyDCRClient(
            client_id="https://example.com/client.json",
            client_secret=None,
            redirect_uris=None,
            cimd_document=cimd_doc,
        )

        manager._assertion_validator.validate_assertion = AsyncMock(return_value=True)

        result = await manager.validate_private_key_jwt(
            "test.jwt.assertion",
            client,
            "https://oauth.example.com/token",
        )
        assert result is True
        manager._assertion_validator.validate_assertion.assert_awaited_once_with(
            "test.jwt.assertion",
            "https://example.com/client.json",
            "https://oauth.example.com/token",
            cimd_doc,
        )


class TestCIMDRedirectUriEnforcement:
    """Tests for CIMD redirect_uri validation security.

    Verifies that CIMD clients enforce BOTH:
    1. CIMD document's redirect_uris
    2. Proxy's allowed_redirect_uri_patterns
    """

    @pytest.fixture
    def mock_dns(self):
        """Mock DNS resolution to return test public IP."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=[TEST_PUBLIC_IP],
        ):
            yield

    async def test_cimd_redirect_uris_enforced(self, httpx_mock, mock_dns):
        """Test that CIMD document redirect_uris are enforced.

        Even if proxy patterns allow http://localhost:*, a CIMD client
        should only accept URIs declared in its document.
        """
        from mcp.shared.auth import InvalidRedirectUriError
        from pydantic import AnyUrl

        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Test App",
            # CIMD only declares port 3000
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "200"},
        )

        # Proxy allows any localhost port
        manager = CIMDClientManager(
            enable_cimd=True,
            allowed_redirect_uri_patterns=["http://localhost:*"],
        )
        client = await manager.get_client(url)
        assert client is not None

        # Declared URI should work
        validated = client.validate_redirect_uri(
            AnyUrl("http://localhost:3000/callback")
        )
        assert str(validated) == "http://localhost:3000/callback"

        # Different port should fail (not in CIMD redirect_uris)
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://localhost:4000/callback"))

    async def test_proxy_patterns_also_checked(self, httpx_mock, mock_dns):
        """Test that proxy patterns are checked even for CIMD clients.

        A CIMD client should not be able to use a redirect_uri that's
        in its document but not allowed by proxy patterns.
        """
        from mcp.shared.auth import InvalidRedirectUriError
        from pydantic import AnyUrl

        url = "https://example.com/client.json"
        doc_data = {
            "client_id": url,
            "client_name": "Test App",
            # CIMD declares both localhost and external URI
            "redirect_uris": [
                "http://localhost:3000/callback",
                "https://evil.com/callback",
            ],
            "token_endpoint_auth_method": "none",
        }
        httpx_mock.add_response(
            json=doc_data,
            headers={"content-length": "200"},
        )

        # Proxy only allows localhost
        manager = CIMDClientManager(
            enable_cimd=True,
            allowed_redirect_uri_patterns=["http://localhost:*"],
        )
        client = await manager.get_client(url)
        assert client is not None

        # Localhost should work (in CIMD and matches pattern)
        validated = client.validate_redirect_uri(
            AnyUrl("http://localhost:3000/callback")
        )
        assert str(validated) == "http://localhost:3000/callback"

        # Evil.com should fail (in CIMD but doesn't match proxy patterns)
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("https://evil.com/callback"))
