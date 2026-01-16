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
from typing import Any, Literal, get_args

import httpx
from pydantic import AnyHttpUrl, SecretStr

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

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
        introspection_url: str,
        client_id: str,
        client_secret: str | SecretStr,
        client_auth_method: ClientAuthMethod = "client_secret_basic",
        timeout_seconds: int = 10,
        required_scopes: list[str] | None = None,
        base_url: AnyHttpUrl | str | None = None,
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
        authenticated using the configured client authentication method (client_secret_basic
        or client_secret_post).

        Args:
            token: The opaque token string to validate

        Returns:
            AccessToken object if valid and active, None if invalid, inactive, or expired
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
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
