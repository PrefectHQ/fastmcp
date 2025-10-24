"""JWT token issuance and verification for FastMCP OAuth Proxy.

This module implements the token factory pattern for OAuth proxies, where the proxy
issues its own JWT tokens to clients instead of forwarding upstream provider tokens.
This maintains proper OAuth 2.0 token audience boundaries.
"""

from __future__ import annotations

import base64
import time
from typing import Any, overload

from authlib.jose import JsonWebToken
from authlib.jose.errors import JoseError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

KDF_ITERATIONS = 1000000


@overload
def derive_jwt_key(*, high_entropy_material: str, salt: str) -> bytes:
    """Derive JWT signing key from a high-entropy key material and server salt."""


@overload
def derive_jwt_key(*, low_entropy_material: str, salt: str) -> bytes:
    """Derive JWT signing key from a low-entropy key material and server salt."""


def derive_jwt_key(
    *,
    high_entropy_material: str | None = None,
    low_entropy_material: str | None = None,
    salt: str,
) -> bytes:
    """Derive JWT signing key from a high-entropy or low-entropy key material and server salt."""
    if high_entropy_material is not None and low_entropy_material is not None:
        raise ValueError(
            "Either high_entropy_material or low_entropy_material must be provided, but not both"
        )

    if high_entropy_material is not None:
        derived_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt.encode(),
            info=b"Fernet",
        ).derive(key_material=high_entropy_material.encode())

        return base64.urlsafe_b64encode(derived_key)

    if low_entropy_material is not None:
        pbkdf2 = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt.encode(),
            iterations=KDF_ITERATIONS,
        ).derive(key_material=low_entropy_material.encode())

        return base64.urlsafe_b64encode(pbkdf2)

    raise ValueError(
        "Either high_entropy_material or low_entropy_material must be provided"
    )


class JWTIssuer:
    """Issues and validates FastMCP-signed JWT tokens using HS256.

    This issuer creates JWT tokens for MCP clients with proper audience claims,
    maintaining OAuth 2.0 token boundaries. Tokens are signed with HS256 using
    a key derived from the upstream client secret.
    """

    def __init__(
        self,
        issuer: str,
        audience: str,
        signing_key: bytes,
    ):
        """Initialize JWT issuer.

        Args:
            issuer: Token issuer (FastMCP server base URL)
            audience: Token audience (typically {base_url}/mcp)
            signing_key: HS256 signing key (32 bytes)
        """
        self.issuer = issuer
        self.audience = audience
        self._signing_key = signing_key
        self._jwt = JsonWebToken(["HS256"])

    def issue_access_token(
        self,
        client_id: str,
        scopes: list[str],
        jti: str,
        expires_in: int = 3600,
    ) -> str:
        """Issue a minimal FastMCP access token.

        FastMCP tokens are reference tokens containing only the minimal claims
        needed for validation and lookup. The JTI maps to the upstream token
        which contains actual user identity and authorization data.

        Args:
            client_id: MCP client ID
            scopes: Token scopes
            jti: Unique token identifier (maps to upstream token)
            expires_in: Token lifetime in seconds

        Returns:
            Signed JWT token
        """
        now = int(time.time())

        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "iss": self.issuer,
            "aud": self.audience,
            "client_id": client_id,
            "scope": " ".join(scopes),
            "exp": now + expires_in,
            "iat": now,
            "jti": jti,
        }

        token_bytes = self._jwt.encode(header, payload, self._signing_key)
        token = token_bytes.decode("utf-8")

        logger.debug(
            "Issued access token for client=%s jti=%s exp=%d",
            client_id,
            jti[:8],
            payload["exp"],
        )

        return token

    def issue_refresh_token(
        self,
        client_id: str,
        scopes: list[str],
        jti: str,
        expires_in: int,
    ) -> str:
        """Issue a minimal FastMCP refresh token.

        FastMCP refresh tokens are reference tokens containing only the minimal
        claims needed for validation and lookup. The JTI maps to the upstream
        token which contains actual user identity and authorization data.

        Args:
            client_id: MCP client ID
            scopes: Token scopes
            jti: Unique token identifier (maps to upstream token)
            expires_in: Token lifetime in seconds (should match upstream refresh expiry)

        Returns:
            Signed JWT token
        """
        now = int(time.time())

        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "iss": self.issuer,
            "aud": self.audience,
            "client_id": client_id,
            "scope": " ".join(scopes),
            "exp": now + expires_in,
            "iat": now,
            "jti": jti,
            "token_use": "refresh",
        }

        token_bytes = self._jwt.encode(header, payload, self._signing_key)
        token = token_bytes.decode("utf-8")

        logger.debug(
            "Issued refresh token for client=%s jti=%s exp=%d",
            client_id,
            jti[:8],
            payload["exp"],
        )

        return token

    def verify_token(self, token: str) -> dict[str, Any]:
        """Verify and decode a FastMCP token.

        Validates JWT signature, expiration, issuer, and audience.

        Args:
            token: JWT token to verify

        Returns:
            Decoded token payload

        Raises:
            JoseError: If token is invalid, expired, or has wrong claims
        """
        try:
            # Decode and verify signature
            payload = self._jwt.decode(token, self._signing_key)

            # Validate expiration
            exp = payload.get("exp")
            if exp and exp < time.time():
                logger.debug("Token expired")
                raise JoseError("Token has expired")

            # Validate issuer
            if payload.get("iss") != self.issuer:
                logger.debug("Token has invalid issuer")
                raise JoseError("Invalid token issuer")

            # Validate audience
            if payload.get("aud") != self.audience:
                logger.debug("Token has invalid audience")
                raise JoseError("Invalid token audience")

            logger.debug(
                "Token verified successfully for subject=%s", payload.get("sub")
            )
            return payload

        except JoseError as e:
            logger.debug("Token validation failed: %s", e)
            raise
