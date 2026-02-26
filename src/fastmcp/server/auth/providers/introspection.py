"""OAuth 2.0 Token Introspection (RFC 7662) provider for FastMCP.

This module provides token verification for opaque tokens using the OAuth 2.0
Token Introspection protocol defined in RFC 7662. It allows FastMCP servers to
validate tokens issued by authorization servers that don't use JWT format.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier

    # Verify opaque tokens via RFC 7662 introspection
    verifier = IntrospectionTokenVerifier(
        introspection_url="https://auth.example.com/oauth/introspect",
        client_id="your-client-id",
        client_secret="your-client-secret",
        required_scopes=["read", "write"]
    )

    mcp = FastMCP("My Protected Server", auth=verifier)
    ```
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Literal, get_args

import httpx
from pydantic import AnyHttpUrl, SecretStr

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


@dataclass
class _IntrospectionCacheEntry:
    """Cached introspection result with expiration."""

    result: AccessToken
    expires_at: float


ClientAuthMethod = Literal["client_secret_basic", "client_secret_post"]


class IntrospectionTokenVerifier(TokenVerifier):
    """
    OAuth 2.0 Token Introspection verifier (RFC 7662).

    This verifier validates opaque tokens by calling an OAuth 2.0 token introspection
    endpoint. Unlike JWT verification which is stateless, token introspection requires
    a network call to the authorization server for each token validation.

    The verifier authenticates to the introspection endpoint using either:
    - HTTP Basic Auth (client_secret_basic, default): credentials in Authorization header
    - POST body authentication (client_secret_post): credentials in request body

    Both methods are specified in RFC 6749 (OAuth 2.0) and RFC 7662 (Token Introspection).

    Use this when:
    - Your authorization server issues opaque (non-JWT) tokens
    - You need to validate tokens from Auth0, Okta, Keycloak, or other OAuth servers
    - Your tokens require real-time revocation checking
    - Your authorization server supports RFC 7662 introspection

    Caching is disabled by default to preserve real-time revocation semantics.
    Set ``cache_ttl_seconds`` to enable caching and reduce load on the
    introspection endpoint (e.g., ``cache_ttl_seconds=300`` for 5 minutes).

    Example:
        ```python
        verifier = IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/oauth/introspect",
            client_id="my-service",
            client_secret="secret-key",
            required_scopes=["api:read"]
        )
        ```
    """

    # Default cache settings
    DEFAULT_MAX_CACHE_SIZE = 10000

    def __init__(
        self,
        *,
        introspection_url: str,
        client_id: str,
        client_secret: str | SecretStr,
        client_auth_method: ClientAuthMethod = "client_secret_basic",
        timeout_seconds: int = 10,
        required_scopes: list[str] | None = None,
        base_url: AnyHttpUrl | str | None = None,
        cache_ttl_seconds: int | None = None,
        max_cache_size: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        """
        Initialize the introspection token verifier.

        Args:
            introspection_url: URL of the OAuth 2.0 token introspection endpoint
            client_id: OAuth client ID for authenticating to the introspection endpoint
            client_secret: OAuth client secret for authenticating to the introspection endpoint
            client_auth_method: Client authentication method. "client_secret_basic" (default)
                uses HTTP Basic Auth header, "client_secret_post" sends credentials in POST body
            timeout_seconds: HTTP request timeout in seconds (default: 10)
            required_scopes: Required scopes for all tokens (optional)
            base_url: Base URL for TokenVerifier protocol
            cache_ttl_seconds: How long to cache introspection results in seconds.
                Caching is disabled by default (None) to preserve real-time
                revocation semantics. Set to a positive integer to enable caching
                (e.g., 300 for 5 minutes).
            max_cache_size: Maximum number of tokens to cache when caching is
                enabled. Default: 10000.
            http_client: Optional httpx.AsyncClient for connection pooling. When provided,
                the client is reused across calls and the caller is responsible for its
                lifecycle. When None (default), a fresh client is created per call.
        """
        # Parse scopes if provided as string
        parsed_required_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else None
        )

        super().__init__(base_url=base_url, required_scopes=parsed_required_scopes)

        self.introspection_url = introspection_url
        self.client_id = client_id
        self.client_secret = (
            client_secret.get_secret_value()
            if isinstance(client_secret, SecretStr)
            else client_secret
        )

        # Validate client_auth_method to catch typos/invalid values early
        valid_methods = get_args(ClientAuthMethod)
        if client_auth_method not in valid_methods:
            options = " or ".join(f"'{m}'" for m in valid_methods)
            raise ValueError(
                f"Invalid client_auth_method: {client_auth_method!r}. "
                f"Must be {options}."
            )
        self.client_auth_method: ClientAuthMethod = client_auth_method

        self.timeout_seconds = timeout_seconds
        self._http_client = http_client
        self.logger = get_logger(__name__)

        # Cache configuration (None or 0 = disabled)
        self._cache_ttl = cache_ttl_seconds or 0
        self._max_cache_size = (
            max_cache_size
            if max_cache_size is not None
            else self.DEFAULT_MAX_CACHE_SIZE
        )
        self._cache: dict[str, _IntrospectionCacheEntry] = {}
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 60  # Cleanup every 60 seconds

    def _hash_token(self, token: str) -> str:
        """Hash token for use as cache key.

        Using SHA-256 for memory efficiency (fixed 64-char hex digest
        regardless of token length).
        """
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _cleanup_expired_cache(self) -> None:
        """Remove expired entries from cache."""
        now = time.time()
        expired = [key for key, entry in self._cache.items() if entry.expires_at < now]
        for key in expired:
            del self._cache[key]
        if expired:
            self.logger.debug("Cleaned up %d expired cache entries", len(expired))

    def _maybe_cleanup(self) -> None:
        """Periodically cleanup expired entries to prevent unbounded growth."""
        now = time.monotonic()
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup_expired_cache()
            self._last_cleanup = now

    def _get_cached(self, token: str) -> tuple[bool, AccessToken | None]:
        """Get cached introspection result.

        Returns:
            Tuple of (is_cached, result):
            - (True, AccessToken) if cached valid token
            - (False, None) if not in cache or expired
        """
        if self._cache_ttl <= 0 or self._max_cache_size <= 0:
            return (False, None)  # Caching disabled

        cache_key = self._hash_token(token)
        entry = self._cache.get(cache_key)

        if entry is None:
            return (False, None)  # Not in cache

        if entry.expires_at < time.time():
            del self._cache[cache_key]
            return (False, None)  # Expired

        # Return a copy to prevent mutations from affecting cached value
        return (True, entry.result.model_copy(deep=True))

    def _set_cached(self, token: str, result: AccessToken) -> None:
        """Cache a valid introspection result with TTL.

        Only successful validations are cached. Failures (inactive, expired,
        missing scopes, errors) are never cached to avoid sticky false negatives.
        """
        if self._cache_ttl <= 0 or self._max_cache_size <= 0:
            return  # Caching disabled

        # Periodic cleanup
        self._maybe_cleanup()

        # Check cache size limit
        if len(self._cache) >= self._max_cache_size:
            self._cleanup_expired_cache()
            # If still at limit after cleanup, evict oldest entry
            if len(self._cache) >= self._max_cache_size:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

        cache_key = self._hash_token(token)

        # Use token's expiration if available and sooner than TTL
        expires_at = time.time() + self._cache_ttl
        if result.expires_at:
            expires_at = min(expires_at, float(result.expires_at))

        # Store a deep copy to prevent mutations from affecting cached value
        self._cache[cache_key] = _IntrospectionCacheEntry(
            result=result.model_copy(deep=True),
            expires_at=expires_at,
        )

    def _create_basic_auth_header(self) -> str:
        """Create HTTP Basic Auth header value from client credentials."""
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    def _extract_scopes(self, introspection_response: dict[str, Any]) -> list[str]:
        """
        Extract scopes from introspection response.

        RFC 7662 allows scopes to be returned as either:
        - A space-separated string in the 'scope' field
        - An array of strings in the 'scope' field (less common but valid)
        """
        scope_value = introspection_response.get("scope")

        if scope_value is None:
            return []

        # Handle string (space-separated) scopes
        if isinstance(scope_value, str):
            return [s.strip() for s in scope_value.split() if s.strip()]

        # Handle array of scopes
        if isinstance(scope_value, list):
            return [str(s) for s in scope_value if s]

        return []

    async def verify_token(self, token: str) -> AccessToken | None:
        """
        Verify a bearer token using OAuth 2.0 Token Introspection (RFC 7662).

        This method makes a POST request to the introspection endpoint with the token,
        authenticated using the configured client authentication method (client_secret_basic
        or client_secret_post).

        Results are cached in-memory to reduce load on the introspection endpoint.
        Cache TTL and size are configurable via constructor parameters.

        Args:
            token: The opaque token string to validate

        Returns:
            AccessToken object if valid and active, None if invalid, inactive, or expired
        """
        # Check cache first
        is_cached, cached_result = self._get_cached(token)
        if is_cached:
            self.logger.debug("Token introspection cache hit")
            return cached_result

        try:
            async with (
                contextlib.nullcontext(self._http_client)
                if self._http_client is not None
                else httpx.AsyncClient(timeout=self.timeout_seconds)
            ) as client:
                # Prepare introspection request per RFC 7662
                # Build request data with token and token_type_hint
                data = {
                    "token": token,
                    "token_type_hint": "access_token",
                }

                # Build headers
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                }

                # Add client authentication based on method
                if self.client_auth_method == "client_secret_basic":
                    headers["Authorization"] = self._create_basic_auth_header()
                elif self.client_auth_method == "client_secret_post":
                    data["client_id"] = self.client_id
                    data["client_secret"] = self.client_secret

                response = await client.post(
                    self.introspection_url,
                    data=data,
                    headers=headers,
                )

                # Check for HTTP errors - don't cache HTTP errors (may be transient)
                if response.status_code != 200:
                    self.logger.debug(
                        "Token introspection failed: HTTP %d - %s",
                        response.status_code,
                        response.text[:200] if response.text else "",
                    )
                    return None

                introspection_data = response.json()

                # Check if token is active (required field per RFC 7662)
                # Don't cache inactive tokens - they may become valid later
                # (e.g., tokens with future nbf, or propagation delays)
                if not introspection_data.get("active", False):
                    self.logger.debug("Token introspection returned active=false")
                    return None

                # Extract client_id (should be present for active tokens)
                client_id = introspection_data.get(
                    "client_id"
                ) or introspection_data.get("sub", "unknown")

                # Extract expiration time
                exp = introspection_data.get("exp")
                if exp:
                    # Validate expiration (belt and suspenders - server should set active=false)
                    if exp < time.time():
                        self.logger.debug(
                            "Token validation failed: expired token for client %s",
                            client_id,
                        )
                        return None

                # Extract scopes
                scopes = self._extract_scopes(introspection_data)

                # Check required scopes
                # Don't cache scope failures - permissions may be updated dynamically
                if self.required_scopes:
                    token_scopes = set(scopes)
                    required_scopes = set(self.required_scopes)
                    if not required_scopes.issubset(token_scopes):
                        self.logger.debug(
                            "Token missing required scopes. Has: %s, Required: %s",
                            token_scopes,
                            required_scopes,
                        )
                        return None

                # Create AccessToken with introspection response data
                result = AccessToken(
                    token=token,
                    client_id=str(client_id),
                    scopes=scopes,
                    expires_at=int(exp) if exp else None,
                    claims=introspection_data,  # Store full response for extensibility
                )
                self._set_cached(token, result)
                return result

        except httpx.TimeoutException:
            self.logger.debug(
                "Token introspection timed out after %d seconds", self.timeout_seconds
            )
            return None
        except httpx.RequestError as e:
            self.logger.debug("Token introspection request failed: %s", e)
            return None
        except Exception as e:
            self.logger.debug("Token introspection error: %s", e)
            return None
