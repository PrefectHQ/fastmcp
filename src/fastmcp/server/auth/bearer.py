"""
Simple JWT Bearer Token validation for hosted MCP servers.

Uses RS256 (asymmetric) where your control plane signs with a private key
and hosted MCP servers validate with the corresponding public key.

Example usage:
# Static public key
provider = BearerTokenValidatorProvider(
    public_key='''-----BEGIN PUBLIC KEY-----
    MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...
    -----END PUBLIC KEY-----''',
    issuer="https://auth.yourservice.com"
)

# Or JWKS URI (recommended for production - allows key rotation)
provider = BearerTokenValidatorProvider(
    jwks_uri="https://auth.yourservice.com/.well-known/jwks.json",
    issuer="https://auth.yourservice.com"
)
"""

import time
from typing import Any

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
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

from fastmcp.server.auth.auth import (
    ClientRegistrationOptions,
    OAuthProvider,
    RevocationOptions,
)


class BearerTokenValidatorProvider(OAuthProvider):
    """
    Simple JWT Bearer Token validator for hosted MCP servers.
    Uses RS256 asymmetric encryption. Supports either static public key
    or JWKS URI for key rotation.
    """

    def __init__(
        self,
        issuer: str,
        public_key: str | None = None,
        jwks_uri: str | None = None,
        audience: str | None = None,
        required_scopes: list[str] | None = None,
    ):
        """
        Initialize the provider.

        Args:
            issuer: Expected issuer claim (your control plane)
            public_key: RSA public key in PEM format (for static key)
            jwks_uri: URI to fetch keys from (for key rotation)
            audience: Expected audience claim (optional)
            required_scopes: List of required scopes for access
        """
        if not (public_key or jwks_uri):
            raise ValueError("Either public_key or jwks_uri must be provided")
        if public_key and jwks_uri:
            raise ValueError("Provide either public_key or jwks_uri, not both")

        super().__init__(
            issuer_url=issuer,
            client_registration_options=ClientRegistrationOptions(enabled=False),
            revocation_options=RevocationOptions(enabled=False),
            required_scopes=required_scopes,
        )

        self.issuer = issuer
        self.audience = audience
        self.public_key = public_key
        self.jwks_uri = jwks_uri
        self.jwt = JsonWebToken(["RS256"])

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

            if not kid:
                raise ValueError("Token missing key ID (kid)")

            return await self._get_jwks_key(kid)

        except Exception as e:
            raise ValueError(f"Failed to extract key ID from token: {e}")

    async def _get_jwks_key(self, kid: str) -> str:
        """Fetch key from JWKS with simple caching."""
        if not self.jwks_uri:
            raise ValueError("JWKS URI not configured")

        current_time = time.time()

        # Check cache
        if (
            current_time - self._jwks_cache_time < self._cache_ttl
            and kid in self._jwks_cache
        ):
            return self._jwks_cache[kid]

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
                if key_kid:
                    jwk = JsonWebKey.import_key(key_data)
                    self._jwks_cache[key_kid] = jwk.get_public_key()

            self._jwks_cache_time = current_time

            if kid not in self._jwks_cache:
                raise ValueError(f"Key ID '{kid}' not found in JWKS")

            return self._jwks_cache[kid]

        except Exception as e:
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

            # Validate expiration
            exp = claims.get("exp")
            if exp and exp < time.time():
                return None

            # Validate issuer
            if claims.get("iss") != self.issuer:
                return None

            # Validate audience if configured
            if self.audience:
                aud = claims.get("aud")
                if isinstance(aud, list):
                    if self.audience not in aud:
                        return None
                elif aud != self.audience:
                    return None

            # Extract claims
            client_id = claims.get("sub") or claims.get("client_id") or "unknown"
            scopes = self._extract_scopes(claims)

            return AccessToken(
                token=token,
                client_id=str(client_id),
                scopes=scopes,
                expires_at=int(exp) if exp else None,
            )

        except JoseError:
            return None
        except Exception:
            return None

    def _extract_scopes(self, claims: dict[str, Any]) -> list[str]:
        """Extract scopes from JWT claims."""
        scope_claim = claims.get("scope", "")
        if isinstance(scope_claim, str):
            return scope_claim.split()
        elif isinstance(scope_claim, list):
            return scope_claim
        return []

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
