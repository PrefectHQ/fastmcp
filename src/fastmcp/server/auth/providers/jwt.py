"""TokenVerifier implementations for FastMCP."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import TypedDict

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class JWKData(TypedDict, total=False):
    """JSON Web Key data structure."""

    kty: str  # Key type (e.g., "RSA") - required
    kid: str  # Key ID (optional but recommended)
    use: str  # Usage (e.g., "sig")
    alg: str  # Algorithm (e.g., "RS256")
    n: str  # Modulus (for RSA keys)
    e: str  # Exponent (for RSA keys)
    x5c: list[str]  # X.509 certificate chain (for JWKs)
    x5t: str  # X.509 certificate thumbprint (for JWKs)


class JWKSData(TypedDict):
    """JSON Web Key Set data structure."""

    keys: list[JWKData]


@dataclass(frozen=True, kw_only=True, repr=False)
class RSAKeyPair:
    """RSA key pair for JWT testing."""

    private_key: SecretStr
    public_key: str

    @classmethod
    def generate(cls) -> RSAKeyPair:
        """
        Generate an RSA key pair for testing.

        Returns:
            RSAKeyPair: Generated key pair
        """
        # Generate private key
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # Serialize private key to PEM format
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        # Serialize public key to PEM format
        public_pem = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("utf-8")
        )

        return cls(
            private_key=SecretStr(private_pem),
            public_key=public_pem,
        )

    def create_token(
        self,
        subject: str = "fastmcp-user",
        issuer: str = "https://fastmcp.example.com",
        audience: str | list[str] | None = None,
        scopes: list[str] | None = None,
        expires_in_seconds: int = 3600,
        additional_claims: dict[str, Any] | None = None,
        kid: str | None = None,
    ) -> str:
        """
        Generate a test JWT token for testing purposes.

        Args:
            subject: Subject claim (usually user ID)
            issuer: Issuer claim
            audience: Audience claim - can be a string or list of strings (optional)
            scopes: List of scopes to include
            expires_in_seconds: Token expiration time in seconds
            additional_claims: Any additional claims to include
            kid: Key ID to include in header
        """
        # Create header
        header = {"alg": "RS256"}
        if kid:
            header["kid"] = kid

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
        jwt_lib = JsonWebToken(["RS256"])
        token_bytes = jwt_lib.encode(
            header, payload, self.private_key.get_secret_value()
        )

        return token_bytes.decode("utf-8")


class JWTVerifierSettings(BaseSettings):
    """Settings for JWT token verification."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_JWT_",
        env_file=".env",
        extra="ignore",
    )

    public_key: str | None = None
    jwks_uri: str | None = None
    issuer: str | None = None
    algorithm: str | None = None
    audience: str | list[str] | None = None
    required_scopes: list[str] | None = None
    base_url: AnyHttpUrl | str | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class JWTVerifier(TokenVerifier):
    """
    JWT token verifier supporting both asymmetric (RSA/ECDSA) and symmetric (HMAC) algorithms.

    This verifier validates JWT tokens using various signing algorithms:
    - **Asymmetric algorithms** (RS256/384/512, ES256/384/512, PS256/384/512):
      Uses public/private key pairs. Ideal for external clients and services where
      only the authorization server has the private key.
    - **Symmetric algorithms** (HS256/384/512): Uses a shared secret for both
      signing and verification. Perfect for internal microservices and trusted
      environments where the secret can be securely shared.

    Use this when:
    - You have JWT tokens issued by an external service (asymmetric)
    - You need JWKS support for automatic key rotation (asymmetric)
    - You have internal microservices sharing a secret key (symmetric)
    - Your tokens contain standard OAuth scopes and claims
    """

    def __init__(
        self,
        *,
        public_key: str | None | NotSetT = NotSet,
        jwks_uri: str | None | NotSetT = NotSet,
        issuer: str | None | NotSetT = NotSet,
        audience: str | list[str] | None | NotSetT = NotSet,
        algorithm: str | None | NotSetT = NotSet,
        required_scopes: list[str] | None | NotSetT = NotSet,
        base_url: AnyHttpUrl | str | None | NotSetT = NotSet,
    ):
        """
        Initialize the JWT token verifier.

        Args:
            public_key: For asymmetric algorithms (RS256, ES256, etc.): PEM-encoded public key.
                       For symmetric algorithms (HS256, HS384, HS512): The shared secret string.
            jwks_uri: URI to fetch JSON Web Key Set (only for asymmetric algorithms)
            issuer: Expected issuer claim
            audience: Expected audience claim(s)
            algorithm: JWT signing algorithm. Supported algorithms:
                      - Asymmetric: RS256/384/512, ES256/384/512, PS256/384/512 (default: RS256)
                      - Symmetric: HS256, HS384, HS512
            required_scopes: Required scopes for all tokens
            base_url: Base URL for TokenVerifier protocol
        """
        settings = JWTVerifierSettings.model_validate(
            {
                k: v
                for k, v in {
                    "public_key": public_key,
                    "jwks_uri": jwks_uri,
                    "issuer": issuer,
                    "audience": audience,
                    "algorithm": algorithm,
                    "required_scopes": required_scopes,
                    "base_url": base_url,
                }.items()
                if v is not NotSet
            }
        )

        if not settings.public_key and not settings.jwks_uri:
            raise ValueError("Either public_key or jwks_uri must be provided")

        if settings.public_key and settings.jwks_uri:
            raise ValueError("Provide either public_key or jwks_uri, not both")

        algorithm = settings.algorithm or "RS256"
        if algorithm not in {
            "HS256",
            "HS384",
            "HS512",
            "RS256",
            "RS384",
            "RS512",
            "ES256",
            "ES384",
            "ES512",
            "PS256",
            "PS384",
            "PS512",
        }:
            raise ValueError(f"Unsupported algorithm: {algorithm}.")

        # Initialize parent TokenVerifier
        super().__init__(
            base_url=settings.base_url,
            required_scopes=settings.required_scopes,
        )

        self.algorithm = algorithm
        self.issuer = settings.issuer
        self.audience = settings.audience
        self.public_key = settings.public_key
        self.jwks_uri = settings.jwks_uri
        self.jwt = JsonWebToken([self.algorithm])
        self.logger = get_logger(__name__)

        # Simple JWKS cache
        self._jwks_cache: dict[str, str] = {}
        self._jwks_cache_time: float = 0
        self._cache_ttl = 3600  # 1 hour

    async def _get_verification_key(self, token: str) -> str:
        """Get the verification key for the token."""
        if self.public_key:
            return self.public_key

        # Extract kid from token header for JWKS lookup
        try:
            import base64
            import json

            header_b64 = token.split(".")[0]
            header_b64 += "=" * (4 - len(header_b64) % 4)  # Add padding
            header = json.loads(base64.urlsafe_b64decode(header_b64))
            kid = header.get("kid")

            return await self._get_jwks_key(kid)

        except Exception as e:
            raise ValueError(f"Failed to extract key ID from token: {e}")

    async def _get_jwks_key(self, kid: str | None) -> str:
        """Fetch key from JWKS with simple caching."""
        if not self.jwks_uri:
            raise ValueError("JWKS URI not configured")

        current_time = time.time()

        # Check cache first
        if current_time - self._jwks_cache_time < self._cache_ttl:
            if kid and kid in self._jwks_cache:
                return self._jwks_cache[kid]
            elif not kid and len(self._jwks_cache) == 1:
                # If no kid but only one key cached, use it
                return next(iter(self._jwks_cache.values()))

        # Fetch JWKS
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.jwks_uri)
                response.raise_for_status()
                jwks_data = response.json()

            # Cache all keys
            self._jwks_cache = {}
            for key_data in jwks_data.get("keys", []):
                key_kid = key_data.get("kid")
                jwk = JsonWebKey.import_key(key_data)
                public_key = jwk.get_public_key()  # type: ignore

                if key_kid:
                    self._jwks_cache[key_kid] = public_key
                else:
                    # Key without kid - use a default identifier
                    self._jwks_cache["_default"] = public_key

            self._jwks_cache_time = current_time

            # Select the appropriate key
            if kid:
                if kid not in self._jwks_cache:
                    self.logger.debug(
                        "JWKS key lookup failed: key ID '%s' not found", kid
                    )
                    raise ValueError(f"Key ID '{kid}' not found in JWKS")
                return self._jwks_cache[kid]
            else:
                # No kid in token - only allow if there's exactly one key
                if len(self._jwks_cache) == 1:
                    return next(iter(self._jwks_cache.values()))
                elif len(self._jwks_cache) > 1:
                    raise ValueError(
                        "Multiple keys in JWKS but no key ID (kid) in token"
                    )
                else:
                    raise ValueError("No keys found in JWKS")

        except httpx.HTTPError as e:
            raise ValueError(f"Failed to fetch JWKS: {e}")
        except Exception as e:
            self.logger.debug(f"JWKS fetch failed: {e}")
            raise ValueError(f"Failed to fetch JWKS: {e}")

    def _extract_scopes(self, claims: dict[str, Any]) -> list[str]:
        """
        Extract scopes from JWT claims. Supports both 'scope' and 'scp'
        claims.

        Checks the `scope` claim first (standard OAuth2 claim), then the `scp`
        claim (used by some Identity Providers).
        """
        for claim in ["scope", "scp"]:
            if claim in claims:
                if isinstance(claims[claim], str):
                    return claims[claim].split()
                elif isinstance(claims[claim], list):
                    return claims[claim]

        return []

    async def load_access_token(self, token: str) -> AccessToken | None:
        """
        Validates the provided JWT bearer token.

        Args:
            token: The JWT token string to validate

        Returns:
            AccessToken object if valid, None if invalid or expired
        """
        try:
            # Get verification key (static or from JWKS)
            verification_key = await self._get_verification_key(token)

            # Decode and verify the JWT token
            claims = self.jwt.decode(token, verification_key)

            # Extract client ID early for logging
            client_id = claims.get("client_id") or claims.get("sub") or "unknown"

            # Validate expiration
            exp = claims.get("exp")
            if exp and exp < time.time():
                self.logger.debug(
                    "Token validation failed: expired token for client %s", client_id
                )
                self.logger.info("Bearer token rejected for client %s", client_id)
                return None

            # Validate issuer - note we use issuer instead of issuer_url here because
            # issuer is optional, allowing users to make this check optional
            if self.issuer:
                if claims.get("iss") != self.issuer:
                    self.logger.debug(
                        "Token validation failed: issuer mismatch for client %s",
                        client_id,
                    )
                    self.logger.info("Bearer token rejected for client %s", client_id)
                    return None

            # Validate audience if configured
            if self.audience:
                aud = claims.get("aud")

                # Handle different combinations of audience types
                audience_valid = False
                if isinstance(self.audience, list):
                    # self.audience is a list - check if any expected audience is present
                    if isinstance(aud, list):
                        # Both are lists - check for intersection
                        audience_valid = any(
                            expected in aud for expected in self.audience
                        )
                    else:
                        # aud is a string - check if it's in our expected list
                        audience_valid = aud in cast(list, self.audience)
                else:
                    # self.audience is a string - use original logic
                    if isinstance(aud, list):
                        audience_valid = self.audience in aud
                    else:
                        audience_valid = aud == self.audience

                if not audience_valid:
                    self.logger.debug(
                        "Token validation failed: audience mismatch for client %s",
                        client_id,
                    )
                    self.logger.info("Bearer token rejected for client %s", client_id)
                    return None

            # Extract scopes
            scopes = self._extract_scopes(claims)

            # Check required scopes
            if self.required_scopes:
                token_scopes = set(scopes)
                required_scopes = set(self.required_scopes)
                if not required_scopes.issubset(token_scopes):
                    self.logger.debug(
                        "Token missing required scopes. Has: %s, Required: %s",
                        token_scopes,
                        required_scopes,
                    )
                    self.logger.info("Bearer token rejected for client %s", client_id)
                    return None

            return AccessToken(
                token=token,
                client_id=str(client_id),
                scopes=scopes,
                expires_at=int(exp) if exp else None,
                claims=claims,
            )

        except JoseError:
            self.logger.debug("Token validation failed: JWT signature/format invalid")
            return None
        except Exception as e:
            self.logger.debug("Token validation failed: %s", str(e))
            return None

    async def verify_token(self, token: str) -> AccessToken | None:
        """
        Verify a bearer token and return access info if valid.

        This method implements the TokenVerifier protocol by delegating
        to our existing load_access_token method.

        Args:
            token: The JWT token string to validate

        Returns:
            AccessToken object if valid, None if invalid or expired
        """
        return await self.load_access_token(token)


class StaticTokenVerifier(TokenVerifier):
    """
    Simple static token verifier for testing and development.

    This verifier validates tokens against a predefined dictionary of valid token
    strings and their associated claims. When a token string matches a key in the
    dictionary, the verifier returns the corresponding claims as if the token was
    validated by a real authorization server.

    Use this when:
    - You're developing or testing locally without a real OAuth server
    - You need predictable tokens for automated testing
    - You want to simulate different users/scopes without complex setup
    - You're prototyping and need simple API key-style authentication

    WARNING: Never use this in production - tokens are stored in plain text!
    """

    def __init__(
        self,
        tokens: dict[str, dict[str, Any]],
        required_scopes: list[str] | None = None,
    ):
        """
        Initialize the static token verifier.

        Args:
            tokens: Dict mapping token strings to token metadata
                   Each token should have: client_id, scopes, expires_at (optional)
            required_scopes: Required scopes for all tokens
        """
        super().__init__(required_scopes=required_scopes)
        self.tokens = tokens

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify token against static token dictionary."""
        token_data = self.tokens.get(token)
        if not token_data:
            return None

        # Check expiration if present
        expires_at = token_data.get("expires_at")
        if expires_at is not None and expires_at < time.time():
            return None

        scopes = token_data.get("scopes", [])

        # Check required scopes
        if self.required_scopes:
            token_scopes = set(scopes)
            required_scopes = set(self.required_scopes)
            if not required_scopes.issubset(token_scopes):
                logger.debug(
                    f"Token missing required scopes. Has: {token_scopes}, Required: {required_scopes}"
                )
                return None

        return AccessToken(
            token=token,
            client_id=token_data["client_id"],
            scopes=scopes,
            expires_at=expires_at,
            claims=token_data,
        )
