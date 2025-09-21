"""Google OAuth provider for FastMCP.

This module provides a complete Google OAuth integration that's ready to use
with just a client ID and client secret. It handles all the complexity of
Google's OAuth flow, token validation, and user management.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.google import GoogleProvider

    # Simple Google OAuth protection
    auth = GoogleProvider(
        client_id="your-google-client-id.apps.googleusercontent.com",
        client_secret="your-google-client-secret"
    )

    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from __future__ import annotations

import time

import httpx
from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.storage import KVStorage
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class GoogleProviderSettings(BaseSettings):
    """Settings for Google OAuth provider."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_GOOGLE_",
        env_file=".env",
        extra="ignore",
    )

    client_id: str | None = None
    client_secret: SecretStr | None = None
    base_url: AnyHttpUrl | str | None = None
    redirect_path: str | None = None
    required_scopes: list[str] | None = None
    timeout_seconds: int | None = None
    allowed_client_redirect_uris: list[str] | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class GoogleTokenVerifier(TokenVerifier):
    """Token verifier for Google OAuth tokens.

    Google OAuth tokens are opaque (not JWTs), so we verify them
    by calling Google's tokeninfo API to check if they're valid and get user info.
    """

    def __init__(
        self,
        *,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
    ):
        """Initialize the Google token verifier.

        Args:
            required_scopes: Required OAuth scopes (e.g., ['openid', 'email'])
            timeout_seconds: HTTP request timeout
        """
        super().__init__(required_scopes=required_scopes)
        self.timeout_seconds = timeout_seconds

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify Google OAuth token by calling Google's tokeninfo API."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                # Use Google's tokeninfo endpoint to validate the token
                response = await client.get(
                    "https://www.googleapis.com/oauth2/v1/tokeninfo",
                    params={"access_token": token},
                    headers={"User-Agent": "FastMCP-Google-OAuth"},
                )

                if response.status_code != 200:
                    logger.debug(
                        "Google token verification failed: %d",
                        response.status_code,
                    )
                    return None

                token_info = response.json()

                # Check if token is expired
                expires_in = token_info.get("expires_in")
                if expires_in and int(expires_in) <= 0:
                    logger.debug("Google token has expired")
                    return None

                # Extract scopes from token info
                scope_string = token_info.get("scope", "")
                token_scopes = [
                    scope.strip() for scope in scope_string.split(" ") if scope.strip()
                ]

                # Check required scopes
                if self.required_scopes:
                    token_scopes_set = set(token_scopes)
                    required_scopes_set = set(self.required_scopes)
                    if not required_scopes_set.issubset(token_scopes_set):
                        logger.debug(
                            "Google token missing required scopes. Has %d, needs %d",
                            len(token_scopes_set),
                            len(required_scopes_set),
                        )
                        return None

                # Get additional user info if we have the right scopes
                user_data = {}
                if "openid" in token_scopes or "profile" in token_scopes:
                    try:
                        userinfo_response = await client.get(
                            "https://www.googleapis.com/oauth2/v2/userinfo",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "User-Agent": "FastMCP-Google-OAuth",
                            },
                        )
                        if userinfo_response.status_code == 200:
                            user_data = userinfo_response.json()
                    except Exception as e:
                        logger.debug("Failed to fetch Google user info: %s", e)

                # Calculate expiration time
                expires_at = None
                if expires_in:
                    expires_at = int(time.time() + int(expires_in))

                # Create AccessToken with Google user info
                access_token = AccessToken(
                    token=token,
                    client_id=token_info.get(
                        "audience", "unknown"
                    ),  # Use audience as client_id
                    scopes=token_scopes,
                    expires_at=expires_at,
                    claims={
                        "sub": user_data.get("id")
                        or token_info.get("user_id", "unknown"),
                        "email": user_data.get("email"),
                        "name": user_data.get("name"),
                        "picture": user_data.get("picture"),
                        "given_name": user_data.get("given_name"),
                        "family_name": user_data.get("family_name"),
                        "locale": user_data.get("locale"),
                        "google_user_data": user_data,
                        "google_token_info": token_info,
                    },
                )
                logger.debug("Google token verified successfully")
                return access_token

        except httpx.RequestError as e:
            logger.debug("Failed to verify Google token: %s", e)
            return None
        except Exception as e:
            logger.debug("Google token verification error: %s", e)
            return None


class GoogleProvider(OAuthProxy):
    """Complete Google OAuth provider for FastMCP.

    This provider makes it trivial to add Google OAuth protection to any
    FastMCP server. Just provide your Google OAuth app credentials and
    a base URL, and you're ready to go.

    Features:
    - Transparent OAuth proxy to Google
    - Automatic token validation via Google's tokeninfo API
    - User information extraction from Google APIs
    - Minimal configuration required

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.google import GoogleProvider

        auth = GoogleProvider(
            client_id="123456789.apps.googleusercontent.com",
            client_secret="GOCSPX-abc123...",
            base_url="https://my-server.com"
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        client_id: str | NotSetT = NotSet,
        client_secret: str | NotSetT = NotSet,
        base_url: AnyHttpUrl | str | NotSetT = NotSet,
        redirect_path: str | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT = NotSet,
        timeout_seconds: int | NotSetT = NotSet,
        allowed_client_redirect_uris: list[str] | NotSetT = NotSet,
        client_storage: KVStorage | None = None,
    ):
        """Initialize Google OAuth provider.

        Args:
            client_id: Google OAuth client ID (e.g., "123456789.apps.googleusercontent.com")
            client_secret: Google OAuth client secret (e.g., "GOCSPX-abc123...")
            base_url: Public URL of your FastMCP server (for OAuth callbacks)
            redirect_path: Redirect path configured in Google OAuth app (defaults to "/auth/callback")
            required_scopes: Required Google scopes (defaults to ["openid"]). Common scopes include:
                - "openid" for OpenID Connect (default)
                - "https://www.googleapis.com/auth/userinfo.email" for email access
                - "https://www.googleapis.com/auth/userinfo.profile" for profile info
            timeout_seconds: HTTP request timeout for Google API calls
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage implementation for OAuth client registrations.
                Defaults to file-based storage if not specified.
        """

        settings = GoogleProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "base_url": base_url,
                    "redirect_path": redirect_path,
                    "required_scopes": required_scopes,
                    "timeout_seconds": timeout_seconds,
                    "allowed_client_redirect_uris": allowed_client_redirect_uris,
                }.items()
                if v is not NotSet
            }
        )

        # Validate required settings
        if not settings.client_id:
            raise ValueError(
                "client_id is required - set via parameter or FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID"
            )
        if not settings.client_secret:
            raise ValueError(
                "client_secret is required - set via parameter or FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET"
            )

        # Apply defaults
        timeout_seconds_final = settings.timeout_seconds or 10
        # Google requires at least one scope - openid is the minimal OIDC scope
        required_scopes_final = settings.required_scopes or ["openid"]
        allowed_client_redirect_uris_final = settings.allowed_client_redirect_uris

        # Create Google token verifier
        token_verifier = GoogleTokenVerifier(
            required_scopes=required_scopes_final,
            timeout_seconds=timeout_seconds_final,
        )

        # Extract secret string from SecretStr
        client_secret_str = (
            settings.client_secret.get_secret_value() if settings.client_secret else ""
        )

        # Initialize OAuth proxy with Google endpoints
        super().__init__(
            upstream_authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            upstream_token_endpoint="https://oauth2.googleapis.com/token",
            upstream_client_id=settings.client_id,
            upstream_client_secret=client_secret_str,
            token_verifier=token_verifier,
            base_url=settings.base_url,
            redirect_path=settings.redirect_path,
            issuer_url=settings.base_url,  # We act as the issuer for client registration
            allowed_client_redirect_uris=allowed_client_redirect_uris_final,
            client_storage=client_storage,
        )

        logger.info(
            "Initialized Google OAuth provider for client %s with scopes: %s",
            settings.client_id,
            required_scopes_final,
        )
