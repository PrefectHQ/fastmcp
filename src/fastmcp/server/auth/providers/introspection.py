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
import time
from typing import Any

import httpx
from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class IntrospectionTokenVerifierSettings(BaseSettings):
    """Settings for OAuth 2.0 Token Introspection verification."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_INTROSPECTION_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    introspection_url: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    timeout_seconds: int = 10
    required_scopes: list[str] | None = None
    base_url: AnyHttpUrl | str | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class IntrospectionTokenVerifier(TokenVerifier):
    """
    OAuth 2.0 Token Introspection verifier (RFC 7662).

    This verifier validates opaque tokens by calling an OAuth 2.0 token introspection
    endpoint. Unlike JWT verification which is stateless, token introspection requires
    a network call to the authorization server for each token validation.

    The verifier authenticates to the introspection endpoint using HTTP Basic Auth
    with the provided client_id and client_secret, as specified in RFC 7662.

    Use this when:
    - Your authorization server issues opaque (non-JWT) tokens
    - You need to validate tokens from Auth0, Okta, Keycloak, or other OAuth servers
    - Your tokens require real-time revocation checking
    - Your authorization server supports RFC 7662 introspection

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

    def __init__(
        self,
        *,
        introspection_url: str | NotSetT = NotSet,
        client_id: str | NotSetT = NotSet,
        client_secret: str | NotSetT = NotSet,
        timeout_seconds: int | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT | None = NotSet,
        base_url: AnyHttpUrl | str | NotSetT | None = NotSet,
    ):
        """
        Initialize the introspection token verifier.

        Args:
            introspection_url: URL of the OAuth 2.0 token introspection endpoint
            client_id: OAuth client ID for authenticating to the introspection endpoint
            client_secret: OAuth client secret for authenticating to the introspection endpoint
            timeout_seconds: HTTP request timeout in seconds (default: 10)
            required_scopes: Required scopes for all tokens (optional)
            base_url: Base URL for TokenVerifier protocol
        """
        settings = IntrospectionTokenVerifierSettings.model_validate(
            {
                k: v
                for k, v in {
                    "introspection_url": introspection_url,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "timeout_seconds": timeout_seconds,
                    "required_scopes": required_scopes,
                    "base_url": base_url,
                }.items()
                if v is not NotSet
            }
        )

        if not settings.introspection_url:
            raise ValueError(
                "introspection_url is required - set via parameter or "
                "FASTMCP_SERVER_AUTH_INTROSPECTION_INTROSPECTION_URL"
            )
        if not settings.client_id:
            raise ValueError(
                "client_id is required - set via parameter or "
                "FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID"
            )
        if not settings.client_secret:
            raise ValueError(
                "client_secret is required - set via parameter or "
                "FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET"
            )

        super().__init__(
            base_url=settings.base_url, required_scopes=settings.required_scopes
        )

        self.introspection_url = settings.introspection_url
        self.client_id = settings.client_id
        self.client_secret = settings.client_secret.get_secret_value()
        self.timeout_seconds = settings.timeout_seconds
        self.logger = get_logger(__name__)

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
        authenticated using HTTP Basic Auth with the client credentials.

        Args:
            token: The opaque token string to validate

        Returns:
            AccessToken object if valid and active, None if invalid, inactive, or expired
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                # Prepare introspection request per RFC 7662
                auth_header = self._create_basic_auth_header()

                response = await client.post(
                    self.introspection_url,
                    data={
                        "token": token,
                        "token_type_hint": "access_token",
                    },
                    headers={
                        "Authorization": auth_header,
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                )

                # Check for HTTP errors
                if response.status_code != 200:
                    self.logger.debug(
                        "Token introspection failed: HTTP %d - %s",
                        response.status_code,
                        response.text[:200] if response.text else "",
                    )
                    return None

                introspection_data = response.json()

                # Check if token is active (required field per RFC 7662)
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
                return AccessToken(
                    token=token,
                    client_id=str(client_id),
                    scopes=scopes,
                    expires_at=int(exp) if exp else None,
                    claims=introspection_data,  # Store full response for extensibility
                )

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
