from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from fastmcp import Client, FastMCP
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.server.auth.providers.jwt import JWKData, JWKSData, JWTVerifier, RSAKeyPair
from fastmcp.utilities.tests import run_server_async


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
        import time

        from authlib.jose import JsonWebToken

        # Create header
        header = {"alg": algorithm}

        # Create payload
        payload = {
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
        jwt_lib = JsonWebToken([algorithm])
        token_bytes = jwt_lib.encode(header, payload, self.secret)

        return token_bytes.decode("utf-8")


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
    """Tests for JWKS URI functionality."""

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
        from authlib.jose import JsonWebKey

        # Create JWK from the RSA public key
        jwk = JsonWebKey.import_key(rsa_key_pair.public_key)
        jwk_data: JWKData = jwk.as_dict()
        jwk_data["kid"] = "test-key-1"
        jwk_data["alg"] = "RS256"

        return {"keys": [jwk_data]}

    async def test_jwks_token_validation(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
    ):
        """Test token validation using JWKS URI."""
        httpx_mock.add_response(
            url="https://test.example.com/.well-known/jwks.json",
            json=mock_jwks_data,
        )

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

    async def test_jwks_token_validation_with_invalid_key(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: JWTVerifier,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
    ):
        httpx_mock.add_response(
            url="https://test.example.com/.well-known/jwks.json",
            json=mock_jwks_data,
        )
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
    ):
        mock_jwks_data["keys"][0]["kid"] = "test-key-1"
        httpx_mock.add_response(
            url="https://test.example.com/.well-known/jwks.json",
            json=mock_jwks_data,
        )
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
    ):
        mock_jwks_data["keys"][0]["kid"] = "test-key-1"
        httpx_mock.add_response(
            url="https://test.example.com/.well-known/jwks.json",
            json=mock_jwks_data,
        )
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
    ):
        mock_jwks_data["keys"][0]["kid"] = "test-key-1"
        httpx_mock.add_response(
            url="https://test.example.com/.well-known/jwks.json",
            json=mock_jwks_data,
        )
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
    ):
        mock_jwks_data["keys"][0]["kid"] = "test-key-1"
        httpx_mock.add_response(
            url="https://test.example.com/.well-known/jwks.json",
            json=mock_jwks_data,
        )
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
    ):
        mock_jwks_data["keys"] = [  # type: ignore[typeddict-item]
            {
                "kid": "test-key-1",
                "alg": "RS256",
            },
            {
                "kid": "test-key-2",
                "alg": "RS256",
            },
        ]

        httpx_mock.add_response(
            url="https://test.example.com/.well-known/jwks.json",
            json=mock_jwks_data,
        )
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

        access_token = await jwks_provider.load_access_token(token)
        assert access_token is None


class TestBearerToken:
    def test_initialization_with_public_key(self, rsa_key_pair: RSAKeyPair):
        """Test provider initialization with public key."""
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key, issuer="https://test.example.com"
        )

        assert provider.issuer == "https://test.example.com"
        assert provider.public_key is not None
        assert provider.jwks_uri is None

    def test_initialization_with_jwks_uri(self):
        """Test provider initialization with JWKS URI."""
        provider = JWTVerifier(
            jwks_uri="https://test.example.com/.well-known/jwks.json",
            issuer="https://test.example.com",
        )

        assert provider.issuer == "https://test.example.com"
        assert provider.jwks_uri == "https://test.example.com/.well-known/jwks.json"
        assert provider.public_key is None

    def test_initialization_requires_key_or_uri(self):
        """Test that either public_key or jwks_uri is required."""
        with pytest.raises(
            ValueError, match="Either public_key or jwks_uri must be provided"
        ):
            JWTVerifier(issuer="https://test.example.com")

    def test_initialization_rejects_both_key_and_uri(self, rsa_key_pair: RSAKeyPair):
        """Test that both public_key and jwks_uri cannot be provided."""
        with pytest.raises(
            ValueError, match="Provide either public_key or jwks_uri, not both"
        ):
            JWTVerifier(
                public_key=rsa_key_pair.public_key,
                jwks_uri="https://test.example.com/.well-known/jwks.json",
                issuer="https://test.example.com",
            )

    async def test_valid_token_validation(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test validation of a valid token."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            scopes=["read", "write"],
        )

        access_token = await bearer_provider.load_access_token(token)

        assert access_token is not None
        assert access_token.client_id == "test-user"
        assert "read" in access_token.scopes
        assert "write" in access_token.scopes
        assert access_token.expires_at is not None

    async def test_expired_token_rejection(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test rejection of expired tokens."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            expires_in_seconds=-3600,  # Expired 1 hour ago
        )

        access_token = await bearer_provider.load_access_token(token)
        assert access_token is None

    async def test_invalid_issuer_rejection(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test rejection of tokens with invalid issuer."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://evil.example.com",  # Wrong issuer
            audience="https://api.example.com",
        )

        access_token = await bearer_provider.load_access_token(token)
        assert access_token is None

    async def test_invalid_audience_rejection(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test rejection of tokens with invalid audience."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://wrong-api.example.com",  # Wrong audience
        )

        access_token = await bearer_provider.load_access_token(token)
        assert access_token is None

    async def test_no_issuer_validation_when_none(self, rsa_key_pair: RSAKeyPair):
        """Test that issuer validation is skipped when provider has no issuer configured."""
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer=None,  # No issuer validation
        )

        token = rsa_key_pair.create_token(
            subject="test-user", issuer="https://any.example.com"
        )

        access_token = await provider.load_access_token(token)
        assert access_token is not None

    async def test_no_audience_validation_when_none(self, rsa_key_pair: RSAKeyPair):
        """Test that audience validation is skipped when provider has no audience configured."""
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.example.com",
            audience=None,  # No audience validation
        )

        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://any-api.example.com",
        )

        access_token = await provider.load_access_token(token)
        assert access_token is not None

    async def test_multiple_audiences_validation(self, rsa_key_pair: RSAKeyPair):
        """Test validation with multiple audiences in token."""
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            additional_claims={
                "aud": ["https://api.example.com", "https://other-api.example.com"]
            },
        )

        access_token = await provider.load_access_token(token)
        assert access_token is not None

    async def test_provider_with_multiple_expected_audiences(
        self, rsa_key_pair: RSAKeyPair
    ):
        """Test provider configured with multiple expected audiences."""
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.example.com",
            audience=["https://api.example.com", "https://other-api.example.com"],
        )

        # Token with single audience that matches one of the expected
        token1 = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )
        access_token1 = await provider.load_access_token(token1)
        assert access_token1 is not None

        # Token with multiple audiences, one of which matches
        token2 = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            additional_claims={
                "aud": ["https://api.example.com", "https://third-party.example.com"]
            },
        )
        access_token2 = await provider.load_access_token(token2)
        assert access_token2 is not None

        # Token with audience that doesn't match any expected
        token3 = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://wrong-api.example.com",
        )
        access_token3 = await provider.load_access_token(token3)
        assert access_token3 is None

    @pytest.mark.parametrize(
        ("iss", "expected"),
        [
            ("https://test.example.com", True),
            ("https://other-issuer.example.com", True),
            ("https://wrong-issuer.example.com", False),
        ],
    )
    async def test_provider_with_multiple_expected_issuers(
        self, rsa_key_pair: RSAKeyPair, iss: str, expected: bool
    ):
        """Provider accepts any issuer from the configured list."""
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer=["https://test.example.com", "https://other-issuer.example.com"],
            audience="https://api.example.com",
        )
        token = rsa_key_pair.create_token(
            subject="test-user", issuer=iss, audience="https://api.example.com"
        )
        access_token = await provider.load_access_token(token)
        assert (access_token is not None) is expected

    async def test_scope_extraction_string(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test scope extraction from space-separated string."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            scopes=["read", "write", "admin"],
        )

        access_token = await bearer_provider.load_access_token(token)

        assert access_token is not None
        assert set(access_token.scopes) == {"read", "write", "admin"}

    async def test_scope_extraction_list(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test scope extraction from list format."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            additional_claims={"scope": ["read", "write"]},  # List format
        )

        access_token = await bearer_provider.load_access_token(token)

        assert access_token is not None
        assert set(access_token.scopes) == {"read", "write"}

    async def test_no_scopes(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test token with no scopes."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            # No scopes
        )

        access_token = await bearer_provider.load_access_token(token)

        assert access_token is not None
        assert access_token.scopes == []

    async def test_scp_claim_extraction_string(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test scope extraction from 'scp' claim with space-separated string."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            additional_claims={"scp": "read write admin"},  # 'scp' claim as string
        )

        access_token = await bearer_provider.load_access_token(token)

        assert access_token is not None
        assert set(access_token.scopes) == {"read", "write", "admin"}

    async def test_scp_claim_extraction_list(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test scope extraction from 'scp' claim with list format."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            additional_claims={
                "scp": ["read", "write", "admin"]
            },  # 'scp' claim as list
        )

        access_token = await bearer_provider.load_access_token(token)

        assert access_token is not None
        assert set(access_token.scopes) == {"read", "write", "admin"}

    async def test_scope_precedence_over_scp(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test that 'scope' claim takes precedence over 'scp' claim when both are present."""
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            additional_claims={
                "scope": "read write",  # Standard OAuth2 claim
                "scp": "admin delete",  # Should be ignored when 'scope' is present
            },
        )

        access_token = await bearer_provider.load_access_token(token)

        assert access_token is not None
        assert set(access_token.scopes) == {"read", "write"}  # Only 'scope' claim used

    async def test_malformed_token_rejection(self, bearer_provider: JWTVerifier):
        """Test rejection of malformed tokens."""
        malformed_tokens = [
            "not.a.jwt",
            "too.many.parts.here.invalid",
            "invalid-token",
            "",
            "header.body",  # Missing signature
        ]

        for token in malformed_tokens:
            access_token = await bearer_provider.load_access_token(token)
            assert access_token is None

    async def test_invalid_signature_rejection(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test rejection of tokens with invalid signatures."""
        # Create a token with a different key pair
        other_key_pair = RSAKeyPair.generate()
        token = other_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

        access_token = await bearer_provider.load_access_token(token)
        assert access_token is None

    async def test_client_id_fallback(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: JWTVerifier
    ):
        """Test client_id extraction with fallback logic."""
        # Test with explicit client_id claim
        token = rsa_key_pair.create_token(
            subject="user123",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            additional_claims={"client_id": "app456"},
        )

        access_token = await bearer_provider.load_access_token(token)
        assert access_token is not None
        assert access_token.client_id == "app456"  # Should prefer client_id over sub

    async def test_string_issuer_validation(self, rsa_key_pair: RSAKeyPair):
        """Test that string (non-URL) issuers are supported per RFC 7519."""
        # Create provider with string issuer
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="my-service",  # String issuer, not a URL
        )

        # Create token with matching string issuer
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="my-service",  # Same string issuer
        )

        access_token = await provider.load_access_token(token)
        assert access_token is not None
        assert access_token.client_id == "test-user"

    async def test_string_issuer_mismatch_rejection(self, rsa_key_pair: RSAKeyPair):
        """Test that mismatched string issuers are rejected."""
        # Create provider with one string issuer
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="my-service",
        )

        # Create token with different string issuer
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="other-service",  # Different string issuer
        )

        access_token = await provider.load_access_token(token)
        assert access_token is None

    async def test_url_issuer_still_works(self, rsa_key_pair: RSAKeyPair):
        """Test that URL issuers still work after the fix."""
        # Create provider with URL issuer
        provider = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://my-auth-server.com",  # URL issuer
        )

        # Create token with matching URL issuer
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://my-auth-server.com",  # Same URL issuer
        )

        access_token = await provider.load_access_token(token)
        assert access_token is not None
        assert access_token.client_id == "test-user"


class TestFastMCPBearerAuth:
    def test_bearer_auth(self):
        mcp = FastMCP(
            auth=JWTVerifier(issuer="https://test.example.com", public_key="abc")
        )
        assert isinstance(mcp.auth, JWTVerifier)

    async def test_unauthorized_access(self, mcp_server_url: str):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url) as client:
                tools = await client.list_tools()  # noqa: F841
        assert isinstance(exc_info.value, httpx.HTTPStatusError)
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    async def test_authorized_access(self, mcp_server_url: str, bearer_token):
        async with Client(mcp_server_url, auth=BearerAuth(bearer_token)) as client:
            tools = await client.list_tools()  # noqa: F841
        assert tools

    async def test_invalid_token_raises_401(self, mcp_server_url: str):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url, auth=BearerAuth("invalid")) as client:
                tools = await client.list_tools()  # noqa: F841
        assert isinstance(exc_info.value, httpx.HTTPStatusError)
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    async def test_expired_token(self, mcp_server_url: str, rsa_key_pair: RSAKeyPair):
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            expires_in_seconds=-3600,
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url, auth=BearerAuth(token)) as client:
                tools = await client.list_tools()  # noqa: F841
        assert isinstance(exc_info.value, httpx.HTTPStatusError)
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    async def test_token_with_bad_signature(self, mcp_server_url: str):
        rsa_key_pair = RSAKeyPair.generate()
        token = rsa_key_pair.create_token()

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url, auth=BearerAuth(token)) as client:
                tools = await client.list_tools()  # noqa: F841
        assert isinstance(exc_info.value, httpx.HTTPStatusError)
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    async def test_token_with_insufficient_scopes(self, rsa_key_pair: RSAKeyPair):
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            scopes=["read"],
        )

        server = create_mcp_server(
            public_key=rsa_key_pair.public_key,
            auth_kwargs=dict(required_scopes=["read", "write"]),
        )

        async with run_server_async(server, transport="http") as mcp_server_url:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                async with Client(mcp_server_url, auth=BearerAuth(token)) as client:
                    tools = await client.list_tools()  # noqa: F841
            # JWTVerifier returns 401 when verify_token returns None (invalid token)
            # This is correct behavior - when TokenVerifier.verify_token returns None,
            # it indicates the token is invalid (not just insufficient permissions)
            assert isinstance(exc_info.value, httpx.HTTPStatusError)
            assert exc_info.value.response.status_code == 401
            assert "tools" not in locals()

    async def test_token_with_sufficient_scopes(self, rsa_key_pair: RSAKeyPair):
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            scopes=["read", "write"],
        )

        server = create_mcp_server(
            public_key=rsa_key_pair.public_key,
            auth_kwargs=dict(required_scopes=["read", "write"]),
        )

        async with run_server_async(server, transport="http") as mcp_server_url:
            async with Client(mcp_server_url, auth=BearerAuth(token)) as client:
                tools = await client.list_tools()
            assert tools


class TestJWTVerifierImport:
    """Test JWT token verifier can be imported and created."""

    def test_jwt_verifier_requires_pyjwt(self):
        """Test that JWTVerifier raises helpful error without PyJWT."""
        # Since PyJWT is likely installed in test environment, we'll just test construction
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        # This should work if PyJWT is available
        try:
            verifier = JWTVerifier(public_key="dummy-key")
            assert verifier.public_key == "dummy-key"
            assert verifier.algorithm == "RS256"
        except ImportError as e:
            # If PyJWT not available, should get helpful error
            assert "PyJWT is required" in str(e)
