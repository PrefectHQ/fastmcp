"""Unit tests for CIMD assertion validators, client manager, and redirect URI enforcement."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import AnyHttpUrl

from fastmcp.server.auth.cimd import (
    CIMDAssertionValidator,
    CIMDClientManager,
    CIMDDocument,
)
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient

# Standard public IP used for DNS mocking in tests
TEST_PUBLIC_IP = "93.184.216.34"


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
