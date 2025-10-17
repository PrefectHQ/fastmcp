"""JWT token issuance and verification for FastMCP OAuth Proxy.

This module implements the token factory pattern for OAuth proxies, where the proxy
issues its own JWT tokens to clients instead of forwarding upstream provider tokens.
This maintains proper OAuth 2.0 token audience boundaries.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from authlib.jose import JsonWebToken
from authlib.jose.errors import JoseError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


def derive_jwt_key(upstream_secret: str, server_salt: str) -> bytes:
    """Derive JWT signing key from upstream client secret and server salt.

    Uses HKDF (RFC 5869) to derive a cryptographically secure signing key from
    the upstream OAuth client secret combined with a server-specific salt.

    Args:
        upstream_secret: The OAuth client secret from upstream provider
        server_salt: Random salt unique to this server instance

    Returns:
        32-byte key suitable for HS256 JWT signing
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=f"fastmcp-jwt-signing-v1-{server_salt}".encode(),
        info=b"HS256",
    ).derive(upstream_secret.encode())


def derive_encryption_key(upstream_secret: str) -> bytes:
    """Derive Fernet encryption key from upstream client secret.

    Uses HKDF to derive a cryptographically secure encryption key for
    encrypting upstream tokens at rest.

    Args:
        upstream_secret: The OAuth client secret from upstream provider

    Returns:
        32-byte Fernet key (base64url-encoded)
    """
    key_material = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"fastmcp-token-encryption-v1",
        info=b"Fernet",
    ).derive(upstream_secret.encode())
    return base64.urlsafe_b64encode(key_material)


def derive_key_from_secret(secret: str | bytes, salt: str, info: bytes) -> bytes:
    """Derive 32-byte key from user-provided secret (string or bytes).

    Accepts any length input and derives a proper cryptographic key.
    Uses HKDF to stretch weak inputs into strong keys.

    Args:
        secret: User-provided secret (any string or bytes)
        salt: Application-specific salt string
        info: Key purpose identifier

    Returns:
        32-byte key suitable for HS256 JWT signing or Fernet encryption
    """
    secret_bytes = secret.encode() if isinstance(secret, str) else secret
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt.encode(),
        info=info,
    ).derive(secret_bytes)


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


class TokenEncryption:
    """Handles encryption/decryption of upstream OAuth tokens at rest."""

    def __init__(self, encryption_key: bytes):
        """Initialize token encryption.

        Args:
            encryption_key: Fernet encryption key (32 bytes, base64url-encoded)
        """
        self._fernet = Fernet(encryption_key)

    def encrypt(self, token: str) -> bytes:
        """Encrypt a token for storage.

        Args:
            token: Plain text token

        Returns:
            Encrypted token bytes
        """
        return self._fernet.encrypt(token.encode())

    def decrypt(self, encrypted_token: bytes) -> str:
        """Decrypt a token from storage.

        Args:
            encrypted_token: Encrypted token bytes

        Returns:
            Plain text token

        Raises:
            cryptography.fernet.InvalidToken: If token is corrupted or key is wrong
        """
        return self._fernet.decrypt(encrypted_token).decode()
