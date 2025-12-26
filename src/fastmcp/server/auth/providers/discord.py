"""Discord OAuth provider for FastMCP.

This module provides a complete Discord OAuth integration that's ready to use
with just a client ID and client secret. It handles all the complexity of
Discord's OAuth flow, token validation, and user management.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.discord import DiscordProvider

    # Simple Discord OAuth protection
    auth = DiscordProvider(
        client_id="your-discord-client-id",
        client_secret="your-discord-client-secret"
    )

    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from __future__ import annotations

import time
from datetime import datetime

import httpx
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class DiscordTokenVerifier(TokenVerifier):
    """Token verifier for Discord OAuth tokens.

    Discord OAuth tokens are opaque (not JWTs), so we verify them
    by calling Discord's tokeninfo API to check if they're valid and get user info.
    """

    def __init__(
        self,
        *,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
    ):
        """Initialize the Discord token verifier.

        Args:
            required_scopes: Required OAuth scopes (e.g., ['email'])
            timeout_seconds: HTTP request timeout
        """
        super().__init__(required_scopes=required_scopes)
        self.timeout_seconds = timeout_seconds

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify Discord OAuth token by calling Discord's tokeninfo API."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                # Use Discord's tokeninfo endpoint to validate the token
                headers = {
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "FastMCP-Discord-OAuth",
                }
                response = await client.get(
                    "https://discord.com/api/oauth2/@me",
                    headers=headers,
                )

                if response.status_code != 200:
                    logger.debug(
                        "Discord token verification failed: %d",
                        response.status_code,
                    )
                    return None

                token_info = response.json()

                # Check if token is expired (Discord returns ISO timestamp)
                expires_str = token_info.get("expires")
                expires_at = None
                if expires_str:
                    expires_dt = datetime.fromisoformat(
                        expires_str.replace("Z", "+00:00")
                    )
                    expires_at = int(expires_dt.timestamp())
                    if expires_at <= int(time.time()):
                        logger.debug("Discord token has expired")
                        return None

                token_scopes = token_info.get("scopes", [])

                # Check required scopes
                if self.required_scopes:
                    token_scopes_set = set(token_scopes)
                    required_scopes_set = set(self.required_scopes)
                    if not required_scopes_set.issubset(token_scopes_set):
                        logger.debug(
                            "Discord token missing required scopes. Has %d, needs %d",
                            len(token_scopes_set),
                            len(required_scopes_set),
                        )
                        return None

                user_data = token_info.get("user", {})
                application = token_info.get("application") or {}
                client_id = str(application.get("id", "unknown"))

                # Create AccessToken with Discord user info
                access_token = AccessToken(
                    token=token,
                    client_id=client_id,
                    scopes=token_scopes,
                    expires_at=expires_at,
                    claims={
                        "sub": user_data.get("id"),
                        "username": user_data.get("username"),
                        "discriminator": user_data.get("discriminator"),
                        "avatar": user_data.get("avatar"),
                        "email": user_data.get("email"),
                        "verified": user_data.get("verified"),
                        "locale": user_data.get("locale"),
                        "discord_user": user_data,
                        "discord_token_info": token_info,
                    },
                )
                logger.debug("Discord token verified successfully")
                return access_token

        except httpx.RequestError as e:
            logger.debug("Failed to verify Discord token: %s", e)
            return None
        except Exception as e:
            logger.debug("Discord token verification error: %s", e)
            return None


class DiscordProvider(OAuthProxy):
    """Complete Discord OAuth provider for FastMCP.

    This provider makes it trivial to add Discord OAuth protection to any
    FastMCP server. Just provide your Discord OAuth app credentials and
    a base URL, and you're ready to go.

    Features:
    - Transparent OAuth proxy to Discord
    - Automatic token validation via Discord's API
    - User information extraction from Discord APIs
    - Minimal configuration required

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.discord import DiscordProvider

        auth = DiscordProvider(
            client_id="123456789",
            client_secret="discord-client-secret-abc123...",
            base_url="https://my-server.com"
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: AnyHttpUrl | str,
        issuer_url: AnyHttpUrl | str | None = None,
        redirect_path: str | None = None,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool = True,
    ):
        """Initialize Discord OAuth provider.

        Args:
            client_id: Discord OAuth client ID (e.g., "123456789")
            client_secret: Discord OAuth client secret (e.g., "S....")
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path configured in Discord OAuth app (defaults to "/auth/callback")
            required_scopes: Required Discord scopes (defaults to ["identify"]). Common scopes include:
                - "identify" for profile info (default)
                - "email" for email access
                - "guilds" for server membership info
            timeout_seconds: HTTP request timeout for Discord API calls (defaults to 10)
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, a DiskStore will be created in the data directory (derived from `platformdirs`). The
                disk store will be encrypted using a key derived from the JWT Signing Key.
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to Discord.
                When False, authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or testing environments.
        """
        # Parse scopes if provided as string
        required_scopes_final = (
            parse_scopes(required_scopes)
            if required_scopes is not None
            else ["identify"]
        )

        # Create Discord token verifier
        token_verifier = DiscordTokenVerifier(
            required_scopes=required_scopes_final,
            timeout_seconds=timeout_seconds,
        )

        # Initialize OAuth proxy with Discord endpoints
        super().__init__(
            upstream_authorization_endpoint="https://discord.com/oauth2/authorize",
            upstream_token_endpoint="https://discord.com/api/oauth2/token",
            upstream_client_id=client_id,
            upstream_client_secret=client_secret,
            token_verifier=token_verifier,
            base_url=base_url,
            redirect_path=redirect_path,
            issuer_url=issuer_url or base_url,  # Default to base_url if not specified
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
        )

        logger.debug(
            "Initialized Discord OAuth provider for client %s with scopes: %s",
            client_id,
            required_scopes_final,
        )
