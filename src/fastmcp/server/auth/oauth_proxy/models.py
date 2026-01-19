"""OAuth Proxy Models and Constants.

This module contains all Pydantic models and constants used by the OAuth proxy.
"""

from __future__ import annotations

import hashlib
from typing import Any, Final

from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl, BaseModel, Field

from fastmcp.server.auth.redirect_validation import validate_redirect_uri

# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

# Default token expiration times
DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS: Final[int] = 60 * 60  # 1 hour
DEFAULT_ACCESS_TOKEN_EXPIRY_NO_REFRESH_SECONDS: Final[int] = (
    60 * 60 * 24 * 365
)  # 1 year
DEFAULT_AUTH_CODE_EXPIRY_SECONDS: Final[int] = 5 * 60  # 5 minutes

# HTTP client timeout
HTTP_TIMEOUT_SECONDS: Final[int] = 30


# -------------------------------------------------------------------------
# Pydantic Models
# -------------------------------------------------------------------------


class OAuthTransaction(BaseModel):
    """OAuth transaction state for consent flow.

    Stored server-side to track active authorization flows with client context.
    Includes CSRF tokens for consent protection per MCP security best practices.
    """

    txn_id: str
    client_id: str
    client_redirect_uri: str
    client_state: str
    code_challenge: str | None
    code_challenge_method: str
    scopes: list[str]
    created_at: float
    resource: str | None = None
    proxy_code_verifier: str | None = None
    csrf_token: str | None = None
    csrf_expires_at: float | None = None


class ClientCode(BaseModel):
    """Client authorization code with PKCE and upstream tokens.

    Stored server-side after upstream IdP callback. Contains the upstream
    tokens bound to the client's PKCE challenge for secure token exchange.
    """

    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str | None
    code_challenge_method: str
    scopes: list[str]
    idp_tokens: dict[str, Any]
    expires_at: float
    created_at: float


class UpstreamTokenSet(BaseModel):
    """Stored upstream OAuth tokens from identity provider.

    These tokens are obtained from the upstream provider (Google, GitHub, etc.)
    and stored in plaintext within this model. Encryption is handled transparently
    at the storage layer via FernetEncryptionWrapper. Tokens are never exposed to MCP clients.
    """

    upstream_token_id: str  # Unique ID for this token set
    access_token: str  # Upstream access token
    refresh_token: str | None  # Upstream refresh token
    refresh_token_expires_at: (
        float | None
    )  # Unix timestamp when refresh token expires (if known)
    expires_at: float  # Unix timestamp when access token expires
    token_type: str  # Usually "Bearer"
    scope: str  # Space-separated scopes
    client_id: str  # MCP client this is bound to
    created_at: float  # Unix timestamp
    raw_token_data: dict[str, Any] = Field(default_factory=dict)  # Full token response


class JTIMapping(BaseModel):
    """Maps FastMCP token JTI to upstream token ID.

    This allows stateless JWT validation while still being able to look up
    the corresponding upstream token when tools need to access upstream APIs.
    """

    jti: str  # JWT ID from FastMCP-issued token
    upstream_token_id: str  # References UpstreamTokenSet
    created_at: float  # Unix timestamp


class RefreshTokenMetadata(BaseModel):
    """Metadata for a refresh token, stored keyed by token hash.

    We store only metadata (not the token itself) for security - if storage
    is compromised, attackers get hashes they can't reverse into usable tokens.
    """

    client_id: str
    scopes: list[str]
    expires_at: int | None = None
    created_at: float


def _hash_token(token: str) -> str:
    """Hash a token for secure storage lookup.

    Uses SHA-256 to create a one-way hash. The original token cannot be
    recovered from the hash, providing defense in depth if storage is compromised.
    """
    return hashlib.sha256(token.encode()).hexdigest()


class ProxyDCRClient(OAuthClientInformationFull):
    """Client for DCR proxy with configurable redirect URI validation.

    This special client class is critical for the OAuth proxy to work correctly
    with Dynamic Client Registration (DCR). Here's why it exists:

    Problem:
    --------
    When MCP clients use OAuth, they dynamically register with random localhost
    ports (e.g., http://localhost:55454/callback). The OAuth proxy needs to:
    1. Accept these dynamic redirect URIs from clients based on configured patterns
    2. Use its own fixed redirect URI with the upstream provider (Google, GitHub, etc.)
    3. Forward the authorization code back to the client's dynamic URI

    Solution:
    ---------
    This class validates redirect URIs against configurable patterns,
    while the proxy internally uses its own fixed redirect URI with the upstream
    provider. This allows the flow to work even when clients reconnect with
    different ports or when tokens are cached.

    Without proper validation, clients could get "Redirect URI not registered" errors
    when trying to authenticate with cached tokens, or security vulnerabilities could
    arise from accepting arbitrary redirect URIs.
    """

    allowed_redirect_uri_patterns: list[str] | None = Field(default=None)
    client_name: str | None = Field(default=None)

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        """Validate redirect URI against allowed patterns.

        Since we're acting as a proxy and clients register dynamically,
        we validate their redirect URIs against configurable patterns.
        This is essential for cached token scenarios where the client may
        reconnect with a different port.
        """
        if redirect_uri is not None:
            # Validate against allowed patterns
            if validate_redirect_uri(
                redirect_uri=redirect_uri,
                allowed_patterns=self.allowed_redirect_uri_patterns,
            ):
                return redirect_uri
            # Fall back to normal validation if not in allowed patterns
            return super().validate_redirect_uri(redirect_uri)
        # If no redirect_uri provided, use default behavior
        return super().validate_redirect_uri(redirect_uri)
