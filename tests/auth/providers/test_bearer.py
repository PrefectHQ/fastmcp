from collections.abc import Generator
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from fastmcp import Client, FastMCP
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.server.auth.providers.bearer import (
    BearerAuthProvider,
    JWKData,
    JWKSData,
    RSAKeyPair,
)
from fastmcp.utilities.tests import run_server_in_process


@pytest.fixture(scope="module")
def rsa_key_pair() -> RSAKeyPair:
    return RSAKeyPair.generate()


@pytest.fixture(scope="module")
def bearer_token(rsa_key_pair: RSAKeyPair) -> str:
    return rsa_key_pair.create_token(
        subject="test-user",
        issuer="https://test.example.com",
        audience="https://api.example.com",
    )


@pytest.fixture
def bearer_provider(rsa_key_pair: RSAKeyPair) -> BearerAuthProvider:
    return BearerAuthProvider(
        public_key=rsa_key_pair.public_key,
        issuer="https://test.example.com",
        audience="https://api.example.com",
    )


def run_mcp_server(
    public_key: str,
    host: str,
    port: int,
    auth_kwargs: dict[str, Any] | None = None,
    run_kwargs: dict[str, Any] | None = None,
) -> None:
    mcp = FastMCP(
        auth=BearerAuthProvider(
            public_key=public_key,
            **auth_kwargs or {},
        )
    )

    @mcp.tool()
    def add(a: int, b: int) -> int:
        return a + b

    mcp.run(host=host, port=port, **run_kwargs or {})


@pytest.fixture(scope="module")
def mcp_server_url(rsa_key_pair: RSAKeyPair) -> Generator[str]:
    with run_server_in_process(
        run_mcp_server,
        public_key=rsa_key_pair.public_key,
        run_kwargs=dict(transport="streamable-http"),
    ) as url:
        yield f"{url}/mcp"


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


class TestBearerTokenJWKS:
    """Tests for JWKS URI functionality."""

    @pytest.fixture
    def jwks_provider(self, rsa_key_pair: RSAKeyPair) -> BearerAuthProvider:
        """Provider configured with JWKS URI."""
        return BearerAuthProvider(
            jwks_uri="https://test.example.com/.well-known/jwks.json",
            issuer="https://test.example.com",
            audience="https://api.example.com",
        )

    @pytest.fixture
    def mock_jwks_data(self, rsa_key_pair: RSAKeyPair) -> JWKSData:
        """Create mock JWKS data from RSA key pair."""
        from authlib.jose import JsonWebKey

        # Create JWK from the RSA public key
        jwk = JsonWebKey.import_key(rsa_key_pair.public_key)  # type: ignore
        jwk_data: JWKData = jwk.as_dict()  # type: ignore
        jwk_data["kid"] = "test-key-1"
        jwk_data["alg"] = "RS256"

        return {"keys": [jwk_data]}

    async def test_jwks_token_validation(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: BearerAuthProvider,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
    ):
        """Test token validation using JWKS URI."""
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

    async def test_jwks_token_validation_with_invalid_key(
        self,
        rsa_key_pair: RSAKeyPair,
        jwks_provider: BearerAuthProvider,
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
        jwks_provider: BearerAuthProvider,
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
        jwks_provider: BearerAuthProvider,
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
        jwks_provider: BearerAuthProvider,
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
        jwks_provider: BearerAuthProvider,
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
        jwks_provider: BearerAuthProvider,
        mock_jwks_data: JWKSData,
        httpx_mock: HTTPXMock,
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
        provider = BearerAuthProvider(
            public_key=rsa_key_pair.public_key, issuer="https://test.example.com"
        )

        assert provider.issuer == "https://test.example.com"
        assert provider.public_key is not None
        assert provider.jwks_uri is None

    def test_initialization_with_jwks_uri(self):
        """Test provider initialization with JWKS URI."""
        provider = BearerAuthProvider(
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
            BearerAuthProvider(issuer="https://test.example.com")

    def test_initialization_rejects_both_key_and_uri(self, rsa_key_pair: RSAKeyPair):
        """Test that both public_key and jwks_uri cannot be provided."""
        with pytest.raises(
            ValueError, match="Provide either public_key or jwks_uri, not both"
        ):
            BearerAuthProvider(
                public_key=rsa_key_pair.public_key,
                jwks_uri="https://test.example.com/.well-known/jwks.json",
                issuer="https://test.example.com",
            )

    async def test_valid_token_validation(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: BearerAuthProvider
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
        self, rsa_key_pair: RSAKeyPair, bearer_provider: BearerAuthProvider
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
        self, rsa_key_pair: RSAKeyPair, bearer_provider: BearerAuthProvider
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
        self, rsa_key_pair: RSAKeyPair, bearer_provider: BearerAuthProvider
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
        provider = BearerAuthProvider(
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
        provider = BearerAuthProvider(
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
        provider = BearerAuthProvider(
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

    async def test_scope_extraction_string(
        self, rsa_key_pair: RSAKeyPair, bearer_provider: BearerAuthProvider
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
        self, rsa_key_pair: RSAKeyPair, bearer_provider: BearerAuthProvider
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
        self, rsa_key_pair: RSAKeyPair, bearer_provider: BearerAuthProvider
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

    async def test_malformed_token_rejection(self, bearer_provider: BearerAuthProvider):
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
        self, rsa_key_pair: RSAKeyPair, bearer_provider: BearerAuthProvider
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
        self, rsa_key_pair: RSAKeyPair, bearer_provider: BearerAuthProvider
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


class TestFastMCPBearerAuth:
    def test_bearer_auth(self):
        mcp = FastMCP(
            auth=BearerAuthProvider(issuer="https://test.example.com", public_key="abc")
        )
        assert isinstance(mcp.auth, BearerAuthProvider)

    async def test_unauthorized_access(self, mcp_server_url: str):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url) as client:
                tools = await client.list_tools()  # noqa: F841
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
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    async def test_token_with_bad_signature(self, mcp_server_url: str):
        rsa_key_pair = RSAKeyPair.generate()
        token = rsa_key_pair.create_token()

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url, auth=BearerAuth(token)) as client:
                tools = await client.list_tools()  # noqa: F841
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    async def test_token_with_insufficient_scopes(
        self, mcp_server_url: str, rsa_key_pair: RSAKeyPair
    ):
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            scopes=["read"],
        )

        with run_server_in_process(
            run_mcp_server,
            public_key=rsa_key_pair.public_key,
            auth_kwargs=dict(required_scopes=["read", "write"]),
            run_kwargs=dict(transport="streamable-http"),
        ) as url:
            mcp_server_url = f"{url}/mcp"
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                async with Client(mcp_server_url, auth=BearerAuth(token)) as client:
                    tools = await client.list_tools()  # noqa: F841
            assert exc_info.value.response.status_code == 403
            assert "tools" not in locals()

    async def test_token_with_sufficient_scopes(
        self, mcp_server_url: str, rsa_key_pair: RSAKeyPair
    ):
        token = rsa_key_pair.create_token(
            subject="test-user",
            issuer="https://test.example.com",
            audience="https://api.example.com",
            scopes=["read", "write"],
        )

        with run_server_in_process(
            run_mcp_server,
            public_key=rsa_key_pair.public_key,
            auth_kwargs=dict(required_scopes=["read", "write"]),
            run_kwargs=dict(transport="streamable-http"),
        ) as url:
            mcp_server_url = f"{url}/mcp"
            async with Client(mcp_server_url, auth=BearerAuth(token)) as client:
                tools = await client.list_tools()
        assert tools
