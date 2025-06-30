import time
from dataclasses import dataclass
from typing import Any

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthToken,
)
from pydantic import AnyHttpUrl, SecretStr, ValidationError
from typing_extensions import TypedDict

from fastmcp.server.auth.auth import (
    ClientRegistrationOptions,
    OAuthProvider,
    RevocationOptions,
)
from fastmcp.utilities.logging import get_logger


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
    private_key: SecretStr
    public_key: str

    @classmethod
    def generate(cls) -> "RSAKeyPair":
        """
        Generate an RSA key pair for testing.

        Returns:
            tuple: (private_key_pem, public_key_pem)
        """
        # Generate private key
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # Get public key
        public_key = private_key.public_key()

        # Serialize private key to PEM format
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        # Serialize public key to PEM format
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

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
            private_key_pem: RSA private key in PEM format
            subject: Subject claim (usually user ID)
            issuer: Issuer claim
            audience: Audience claim - can be a string or list of strings (optional)
            scopes: List of scopes to include
            expires_in_seconds: Token expiration time in seconds
            additional_claims: Any additional claims to include
            kid: Key ID for JWKS lookup (optional)

        Returns:
            Signed JWT token string
        """
        # TODO : Add support for configurable algorithms
        jwt = JsonWebToken(["RS256"])

        now = int(time.time())

        # Build payload
        payload = {
            "iss": issuer,
            "sub": subject,
            "iat": now,
            "exp": now + expires_in_seconds,
        }

        if audience:
            payload["aud"] = audience

        if scopes:
            payload["scope"] = " ".join(scopes)

        if additional_claims:
            payload.update(additional_claims)

        # Create header
        header = {"alg": "RS256"}
        if kid:
            header["kid"] = kid

        # Sign and return token
        token_bytes = jwt.encode(
            header,
            payload,
            key=self.private_key.get_secret_value(),
        )
        return token_bytes.decode("utf-8")


class BearerAuthProvider(OAuthProvider):
    """
    Simple JWT Bearer Token validator for hosted MCP servers.
    Uses RS256 asymmetric encryption by default but supports all JWA algorithms. Supports either static public key
    or JWKS URI for key rotation.

    Note that this provider DOES NOT permit client registration or revocation, or any OAuth flows.
    It is intended to be used with a control plane that manages clients and tokens.
    """

    def __init__(
        self,
        public_key: str | None = None,
        jwks_uri: str | None = None,
        issuer: str | None = None,
        algorithm: str | None = None,
        audience: str | list[str] | None = None,
        required_scopes: list[str] | None = None,
    ):
        """
        Initialize the provider. Either public_key or jwks_uri must be provided.

        Args:
            public_key: RSA public key in PEM format (for static key)
            jwks_uri: URI to fetch keys from (for key rotation)
            issuer: Expected issuer claim (optional)
            algorithm: Algorithm to use for verification (optional, defaults to RS256)
            audience: Expected audience claim - can be a string or list of strings (optional)
            required_scopes: List of required scopes for access (optional)
        """
        if not (public_key or jwks_uri):
            raise ValueError("Either public_key or jwks_uri must be provided")
        if public_key and jwks_uri:
            raise ValueError("Provide either public_key or jwks_uri, not both")

        if not algorithm:
            algorithm = "RS256"
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

        # Only pass issuer to parent if it's a valid URL, otherwise use default
        # This allows the issuer claim validation to work with string issuers per RFC 7519
        try:
            issuer_url = AnyHttpUrl(issuer) if issuer else "https://fastmcp.example.com"
        except ValidationError:
            # Issuer is not a valid URL, use default for parent class
            issuer_url = "https://fastmcp.example.com"

        super().__init__(
            issuer_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(enabled=False),
            revocation_options=RevocationOptions(enabled=False),
            required_scopes=required_scopes,
        )

        self.algorithm = algorithm
        self.issuer = issuer
        self.audience = audience
        self.public_key = public_key
        self.jwks_uri = jwks_uri
        self.jwt = JsonWebToken([self.algorithm])  # Use RS256 by default
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

        except Exception as e:
            self.logger.debug("JWKS fetch failed: %s", str(e))
            raise ValueError(f"Failed to fetch JWKS: {e}")

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
                        audience_valid = aud in self.audience
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

            return AccessToken(
                token=token,
                client_id=str(client_id),
                scopes=scopes,
                expires_at=int(exp) if exp else None,
            )

        except JoseError:
            self.logger.debug("Token validation failed: JWT signature/format invalid")
            return None
        except Exception as e:
            self.logger.debug("Token validation failed: %s", str(e))
            return None

    def _extract_scopes(self, claims: dict[str, Any]) -> list[str]:
        """Extract scopes from JWT claims."""
        scope_claim = claims.get("scope", "")
        if isinstance(scope_claim, str):
            return scope_claim.split()
        elif isinstance(scope_claim, list):
            return scope_claim
        return []

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

    # --- Unused OAuth server methods ---
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raise NotImplementedError("Client management not supported")

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError("Client registration not supported")

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        raise NotImplementedError("Authorization flow not supported")

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        raise NotImplementedError("Authorization code flow not supported")

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        raise NotImplementedError("Authorization code exchange not supported")

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        raise NotImplementedError("Refresh token flow not supported")

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        raise NotImplementedError("Refresh token exchange not supported")

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        raise NotImplementedError("Token revocation not supported")
