import time
from collections.abc import AsyncGenerator
from typing import Any, cast
from unittest.mock import patch

import pytest
from joserfc import jwk as jose_jwk
from joserfc import jwt
from joserfc.jws import JWSRegistry
from joserfc.registry import HeaderParameter

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWKData, JWKSData, JWTVerifier, RSAKeyPair
from fastmcp.utilities.tests import run_server_async
from tests.utilities.httpx2_mock import HTTPXMock

# Standard public IP used for DNS mocking in tests
TEST_PUBLIC_IP = "93.184.216.34"


class SymmetricKeyHelper:
    """Helper class for generating symmetric key JWT tokens for testing."""

    def __init__(self, secret: str):
        """Initialize with a secret key."""
        self.secret = secret

    def create_token(
        self,
        subject: str = "fastmcp-user",
        issuer: str = "https://fastmcp.example.com",
        audience: str | list[str] | None = None,
        scopes: list[str] | None = None,
        expires_in_seconds: int = 3600,
        additional_claims: dict[str, Any] | None = None,
        algorithm: str = "HS256",
    ) -> str:
        """
        Generate a test JWT token using symmetric key for testing purposes.

        Args:
            subject: Subject claim (usually user ID)
            issuer: Issuer claim
            audience: Audience claim - can be a string or list of strings (optional)
            scopes: List of scopes to include
            expires_in_seconds: Token expiration time in seconds
            additional_claims: Any additional claims to include
            algorithm: JWT signing algorithm (HS256, HS384, or HS512)
        """
        # Create header
        header = {"alg": algorithm}

        # Create payload
        payload: dict[str, str | int | list[str]] = {
            "sub": subject,
            "iss": issuer,
            "iat": int(time.time()),
            "exp": int(time.time()) + expires_in_seconds,
        }

        if audience:
            payload["aud"] = audience

        if scopes:
            payload["scope"] = " ".join(scopes)

        if additional_claims:
            payload.update(additional_claims)

        # Create JWT
        signing_key = jose_jwk.import_key(self.secret, "oct")
        token = jwt.encode(header, payload, signing_key, algorithms=[algorithm])

        return token


@pytest.fixture(scope="module")
def rsa_key_pair() -> RSAKeyPair:
    return RSAKeyPair.generate()


@pytest.fixture(scope="module")
def symmetric_key_helper() -> SymmetricKeyHelper:
    """Generate a symmetric key helper for testing."""
    return SymmetricKeyHelper("test-secret-key-for-hmac-signing")


@pytest.fixture(scope="module")
def bearer_token(rsa_key_pair: RSAKeyPair) -> str:
    return rsa_key_pair.create_token(
        subject="test-user",
        issuer="https://test.example.com",
        audience="https://api.example.com",
    )


@pytest.fixture
def bearer_provider(rsa_key_pair: RSAKeyPair) -> JWTVerifier:
    return JWTVerifier(
        public_key=rsa_key_pair.public_key,
        issuer="https://test.example.com",
        audience="https://api.example.com",
    )


@pytest.fixture
def symmetric_provider(symmetric_key_helper: SymmetricKeyHelper) -> JWTVerifier:
    """Create JWTVerifier configured for symmetric key verification."""
    return JWTVerifier(
        public_key=symmetric_key_helper.secret,
        issuer="https://test.example.com",
        audience="https://api.example.com",
        algorithm="HS256",
    )


def create_mcp_server(
    public_key: str,
    auth_kwargs: dict[str, Any] | None = None,
) -> FastMCP:
    mcp = FastMCP(
        auth=JWTVerifier(
            public_key=public_key,
            **auth_kwargs or {},
        )
    )

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    return mcp


@pytest.fixture
async def mcp_server_url(rsa_key_pair: RSAKeyPair) -> AsyncGenerator[str, None]:
    server = create_mcp_server(
        public_key=rsa_key_pair.public_key,
        auth_kwargs=dict(
            issuer="https://test.example.com",
            audience="https://api.example.com",
        ),
    )
    async with run_server_async(server, transport="http") as url:
        yield url


class TestRSAKeyPair:
    def test_generate_key_pair(self):
        """Test RSA key pair generation."""
        key_pair = RSAKeyPair.generate()

        assert key_pair.private_key is not None
        assert key_pair.public_key is not None

        # Check that keys are in PEM format
        private_pem = key_pair.private_key.get_secret_value()
        public_pem = key_pair.public_key

        assert "-----BEGIN PRIVATE KEY-----" in private_pem
        assert "-----END PRIVATE KEY-----" in private_pem
        assert "-----BEGIN PUBLIC KEY-----" in public_pem
        assert "-----END PUBLIC KEY-----" in public_pem

    def test_create_basic_token(self, rsa_key_pair: RSAKeyPair):
        """Test basic token creation."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
        )

        assert isinstance(token, str)
        assert len(token.split(".")) == 3  # JWT has 3 parts

    def test_create_token_with_scopes(self, rsa_key_pair: RSAKeyPair):
        """Test token creation with scopes."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            scopes=["read", "write"],
        )

        assert isinstance(token, str)
        # We'll validate the scopes in the BearerToken tests


class TestJWTVerifierHeaders:
    async def test_non_critical_private_header_is_allowed(
        self, rsa_key_pair: RSAKeyPair
    ):
        signing_key = jose_jwk.import_key(
            rsa_key_pair.private_key.get_secret_value(),
            "RSA",
        )
        token = jwt.encode(
            {
                "alg": "RS256",
                "cat": "cl_example",
            },
            {
                "sub": "test-user",
                "iss": "https://test.example.com",
                "exp": int(time.time()) + 3600,
            },
            signing_key,
            algorithms=["RS256"],
            registry=JWSRegistry(strict_check_header=False),
        )
        verifier = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.example.com",
        )

        access_token = await verifier.verify_token(token)

        assert access_token is not None
        assert access_token.client_id == "test-user"

    async def test_critical_private_header_is_rejected(self, rsa_key_pair: RSAKeyPair):
        signing_key = jose_jwk.import_key(
            rsa_key_pair.private_key.get_secret_value(),
            "RSA",
        )
        token = jwt.encode(
            {
                "alg": "RS256",
                "crit": ["cat"],
                "cat": "cl_example",
            },
            {
                "sub": "test-user",
                "iss": "https://test.example.com",
                "exp": int(time.time()) + 3600,
            },
            signing_key,
            algorithms=["RS256"],
            registry=JWSRegistry(
                header_registry={
                    "cat": HeaderParameter("Custom private header", "str")
                },
                strict_check_header=False,
            ),
        )
        verifier = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.example.com",
        )

        access_token = await verifier.verify_token(token)

        assert access_token is None


class TestSymmetricKeyJWT:
    """Tests for JWT verification using symmetric keys (HMAC algorithms)."""

    def test_initialization_with_symmetric_key(
        self, symmetric_key_helper: SymmetricKeyHelper
    ):
        """Test JWTVerifier initialization with symmetric key."""
        provider = JWTVerifier(
            public_key=symmetric_key_helper.secret,
            issuer="https://test.example.com",
            algorithm="HS256",
        )

        assert provider.issuer == "https://test.example.com"
        assert provider.public_key == symmetric_key_helper.secret
        assert provider.algorithm == "HS256"
        assert provider.jwks_uri is None

    def test_initialization_rejects_hs_algorithm_with_jwks_uri(self):
        """Test that HMAC algorithms cannot be used with JWKS URI."""
        with pytest.raises(ValueError, match="cannot be used with jwks_uri"):
            JWTVerifier(
                jwks_uri="https://test.example.com/.well-known/jwks.json",
                issuer="https://test.example.com",
                algorithm="HS256",
            )

    def test_initialization_with_different_symmetric_algorithms(
        self, symmetric_key_helper: SymmetricKeyHelper
    ):
        """Test JWTVerifier initialization with different HMAC algorithms."""
        algorithms = ["HS256", "HS384", "HS512"]

        for algorithm in algorithms:
            provider = JWTVerifier(
                public_key=symmetric_key_helper.secret,
                issuer="https://test.example.com",
                algorithm=algorithm,
            )
            assert provider.algorithm == algorithm

    def test_symmetric_algorithm_rejects_jwks_uri(self):
        """HS* algorithms must not be configured with JWKS/public key endpoints."""
        with pytest.raises(ValueError, match="cannot be used with jwks_uri"):
            JWTVerifier(
                jwks_uri="https://test.example.com/.well-known/jwks.json",
                issuer="https://test.example.com",
                algorithm="HS256",
            )

    def test_symmetric_algorithm_rejects_pem_public_key(self, rsa_key_pair: RSAKeyPair):
        """HS* algorithms must use a shared secret, not PEM public key material."""
        with pytest.raises(ValueError, match="require a shared secret"):
            JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.example.com",
                algorithm="HS256",
            )

    def test_symmetric_algorithm_accepts_bytes_secret(self):
        """HS* algorithms accept bytes secrets without TypeError."""
        verifier = JWTVerifier(
            public_key=b"secret",
            algorithm="HS256",
        )
        assert verifier.algorithm == "HS256"

    async def test_valid_symmetric_token_validation(
        self, symmetric_key_helper: SymmetricKeyHelper, symmetric_provider: JWTVerifier
    ):
        """Test validation of a valid token signed with symmetric key."""
        token = symmetric_key_helper.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            scopes=["read", "write"],
            algorithm="HS256",
        )

        access_token = await symmetric_provider.load_access_token(token)

        assert access_token is not None
        assert access_token.client_id == "test-user"
        assert "read" in access_token.scopes
        assert "write" in access_token.scopes
        assert access_token.expires_at is not None

    async def test_symmetric_token_with_different_algorithms(
        self, symmetric_key_helper: SymmetricKeyHelper
    ):
        """Test that different HMAC algorithms work correctly."""
        algorithms = ["HS256", "HS384", "HS512"]

        for algorithm in algorithms:
            provider = JWTVerifier(
                public_key=symmetric_key_helper.secret,
                issuer="https://test.example.com",
                algorithm=algorithm,
            )

            token = symmetric_key_helper.create_token(
                subject="test-user",
                issuer="https://test.example.com",
                algorithm=algorithm,
            )

            access_token = await provider.load_access_token(token)
            assert access_token is not None
            assert access_token.client_id == "test-user"

    async def test_symmetric_token_issuer_validation(
        self, symmetric_key_helper: SymmetricKeyHelper, symmetric_provider: JWTVerifier
    ):
        """Test issuer validation with symmetric key tokens."""
        # Valid issuer
        valid_token = symmetric_key_helper.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )
        access_token = await symmetric_provider.load_access_token(valid_token)
        assert access_token is not None

        # Invalid issuer
        invalid_token = symmetric_key_helper.create_token(
            subject="test-user",
            issuer="https://evil.example.com",
            audience="https://api.example.com",
        )
        access_token = await symmetric_provider.load_access_token(invalid_token)
        assert access_token is None

    async def test_symmetric_token_audience_validation(
        self, symmetric_key_helper: SymmetricKeyHelper, symmetric_provider: JWTVerifier
    ):
        """Test audience validation with symmetric key tokens."""
        # Valid audience
        valid_token = symmetric_key_helper.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )
        access_token = await symmetric_provider.load_access_token(valid_token)
        assert access_token is not None

        # Invalid audience
        invalid_token = symmetric_key_helper.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://wrong-api.example.com",
        )
        access_token = await symmetric_provider.load_access_token(invalid_token)
        assert access_token is None

    async def test_symmetric_token_scope_extraction(
        self, symmetric_key_helper: SymmetricKeyHelper, symmetric_provider: JWTVerifier
    ):
        """Test scope extraction from symmetric key tokens."""
        token = symmetric_key_helper.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            scopes=["read", "write", "admin"],
        )

        access_token = await symmetric_provider.load_access_token(token)
        assert access_token is not None
        assert set(access_token.scopes) == {"read", "write", "admin"}

    async def test_symmetric_token_expiration(
        self, symmetric_key_helper: SymmetricKeyHelper, symmetric_provider: JWTVerifier
    ):
        """Test expiration validation with symmetric key tokens."""
        # Valid token
        valid_token = symmetric_key_helper.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            expires_in_seconds=3600,  # 1 hour from now
        )
        access_token = await symmetric_provider.load_access_token(valid_token)
        assert access_token is not None

        # Expired token
        expired_token = symmetric_key_helper.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            expires_in_seconds=-3600,  # 1 hour ago
        )
        access_token = await symmetric_provider.load_access_token(expired_token)
        assert access_token is None

    async def test_symmetric_token_invalid_signature(
        self, symmetric_key_helper: SymmetricKeyHelper, symmetric_provider: JWTVerifier
    ):
        """Test rejection of tokens with invalid signatures."""
        # Create a token with a different secret
        other_helper = SymmetricKeyHelper("different-secret-key")
        token = other_helper.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

        access_token = await symmetric_provider.load_access_token(token)
        assert access_token is None

    async def test_symmetric_token_algorithm_mismatch(
        self, symmetric_key_helper: SymmetricKeyHelper
    ):
        """Test that tokens with mismatched algorithms are rejected."""
        # Create provider expecting HS256
        provider = JWTVerifier(
            public_key=symmetric_key_helper.secret,
            issuer="https://test.example.com",
            algorithm="HS256",
        )

        # Create token with HS512
        token = symmetric_key_helper.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            algorithm="HS512",
        )

        # Should fail because provider expects HS256
        access_token = await provider.load_access_token(token)
        assert access_token is None


class TestBearerTokenJWKS:
    """Tests for JWKS URI functionality.

    Note: With SSRF protection, JWKS fetches validate DNS and connect to the
    resolved IP. Tests mock DNS resolution to return a public IP.
    """

    @pytest.fixture
    def jwks_provider(self, rsa_key_pair: RSAKeyPair) -> JWTVerifier:
        """Provider configured with JWKS URI."""
        return JWTVerifier(
            jwks_uri="https://test.example.com/.well-known/jwks.json",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

    @pytest.fixture
    def mock_jwks_data(self, rsa_key_pair: RSAKeyPair) -> JWKSData:
        """Create mock JWKS data from RSA key pair."""
        # Create JWK from the RSA public key
        public_key = jose_jwk.import_key(rsa_key_pair.public_key, "RSA")
        public_key_data = public_key.as_dict()
        kty = public_key_data["kty"]
        n = public_key_data["n"]
        e = public_key_data["e"]
        assert isinstance(kty, str)
        assert isinstance(n, str)
        assert isinstance(e, str)
        jwk_data = JWKData(
            kty=kty,
            n=n,
            e=e,
            kid="test-key-1",
            alg="RS256",
        )

        return {"keys": [jwk_data]}

    @pytest.fixture
    def mock_dns(self):
        """Mock DNS resolution to return test public IP."""
        with patch(
            "fastmcp.server.auth.ssrf.resolve_hostname",
            return_value=[TEST_PUBLIC_IP],
        ):
            yield

    async def test_jwks_token_validation(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
        mock_dns,
    ):
        """Test token validation using JWKS URI."""
        httpx_mock.add_response(json=mock_jwks_data)

        username = "test-user"
        issuer = "https://test.example.com"
        audience = "https://api.example.com"

        token = rsa_key_pair.create_token(
            subject=username,
            issuer=issuer,
            audience=audience,
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is not None
        assert access_token.client_id == username

        # ensure the raw claims are present - #1398
        assert access_token.claims.get("sub") == username
        assert access_token.claims.get("iss") == issuer
        assert access_token.claims.get("aud") == audience

    async def test_jwks_skips_unsupported_key_types(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
        mock_dns,
    ):
        """An unsupported key type in the JWKS (e.g. OKP/Ed25519) must be
        skipped, not poison the whole key set - #4515.

        Some authorization servers (e.g. Rauthy, Ory Hydra) publish an
        Ed25519 key alongside RSA keys; tokens signed by the RSA keys must
        still verify.
        """
        okp_key = cast(
            "JWKData",
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "kid": "ed25519-key",
                "alg": "EdDSA",
                "use": "sig",
                "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
            },
        )
        mock_jwks_data["keys"][0]["kid"] = "test-key-1"
        # Unsupported key FIRST, so an unguarded conversion loop would
        # abort before reaching the RSA key the token needs
        mock_jwks_data["keys"].insert(0, okp_key)
        httpx_mock.add_response(json=mock_jwks_data)

        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            kid="test-key-1",
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is not None
        assert access_token.client_id == "test-user"

    async def test_jwks_with_only_unsupported_keys_rejects_cleanly(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        httpx_mock: HTTPXMock,
        mock_dns,
    ):
        """If every key in the JWKS is unsupported, verification fails
        cleanly (returns None) rather than crashing - #4515."""
        okp_only = {
            "keys": [
                cast(
                    "JWKData",
                    {
                        "kty": "OKP",
                        "crv": "Ed25519",
                        "kid": "ed25519-key",
                        "alg": "EdDSA",
                        "use": "sig",
                        "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
                    },
                )
            ]
        }
        httpx_mock.add_response(json=okp_only)

        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            kid="ed25519-key",
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is None

    async def test_jwks_token_validation_with_invalid_key(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
        mock_dns,
    ):
        httpx_mock.add_response(json=mock_jwks_data)
        token = RSAKeyPair.generate().create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is None

    async def test_jwks_token_validation_with_kid(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
        mock_dns,
    ):
        mock_jwks_data["keys"][0]["kid"] = "test-key-1"
        httpx_mock.add_response(json=mock_jwks_data)
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            kid="test-key-1",
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is not None
        assert access_token.client_id == "test-user"

    async def test_jwks_token_validation_with_kid_and_no_kid_in_token(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
        mock_dns,
    ):
        mock_jwks_data["keys"][0]["kid"] = "test-key-1"
        httpx_mock.add_response(json=mock_jwks_data)
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is not None
        assert access_token.client_id == "test-user"

    async def test_jwks_token_validation_with_no_kid_and_kid_in_jwks(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
        mock_dns,
    ):
        mock_jwks_data["keys"][0]["kid"] = "test-key-1"
        httpx_mock.add_response(json=mock_jwks_data)
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is not None
        assert access_token.client_id == "test-user"

    async def test_jwks_token_validation_with_kid_mismatch(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
        mock_dns,
    ):
        mock_jwks_data["keys"][0]["kid"] = "test-key-1"
        httpx_mock.add_response(json=mock_jwks_data)
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            kid="test-key-2",
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is None

    async def test_jwks_token_validation_with_multiple_keys_and_no_kid_in_token(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
        mock_dns,
    ):
        mock_jwks_data["keys"] = [
            {
                "kid": "test-key-1",
                "alg": "RS256",
            },
            {
                "kid": "test-key-2",
                "alg": "RS256",
            },
        ]

        httpx_mock.add_response(json=mock_jwks_data)
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is None
