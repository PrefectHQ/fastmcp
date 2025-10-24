"""GitHub OAuth provider for FastMCP.

This module provides a complete GitHub OAuth integration that's ready to use
with just a client ID and client secret. It handles all the complexity of
GitHub's OAuth flow, token validation, and user management.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.github import GitHubProvider

    # Simple GitHub OAuth protection
    auth = GitHubProvider(
        client_id="your-github-client-id",
        client_secret="your-github-client-secret"
    )

    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from __future__ import annotations

import httpx
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class GitHubProviderSettings(BaseSettings):
    """Settings for GitHub OAuth provider."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_GITHUB_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    client_id: str | None = None
    client_secret: SecretStr | None = None
    base_url: AnyHttpUrl | str | None = None
    issuer_url: AnyHttpUrl | str | None = None
    redirect_path: str | None = None
    required_scopes: list[str] | None = None
    timeout_seconds: int | None = None
    allowed_client_redirect_uris: list[str] | None = None
    jwt_signing_key: str | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class GitHubTokenVerifier(TokenVerifier):
    """Token verifier for GitHub OAuth tokens.

    GitHub OAuth tokens are opaque (not JWTs), so we verify them
    by calling GitHub's API to check if they're valid and get user info.
    """

    def __init__(
        self,
        *,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
    ):
        """Initialize the GitHub token verifier.

        Args:
            required_scopes: Required OAuth scopes (e.g., ['user:email'])
            timeout_seconds: HTTP request timeout
        """
        super().__init__(required_scopes=required_scopes)
        self.timeout_seconds = timeout_seconds

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify GitHub OAuth token by calling GitHub API."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                # Get token info from GitHub API
                response = await client.get(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github.v3+json",
                        "User-Agent": "FastMCP-GitHub-OAuth",
                    },
                )

                if response.status_code != 200:
                    logger.debug(
                        "GitHub token verification failed: %d - %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return None

                user_data = response.json()

                # Get token scopes from GitHub API
                # GitHub includes scopes in the X-OAuth-Scopes header
                scopes_response = await client.get(
                    "https://api.github.com/user/repos",  # Any authenticated endpoint
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github.v3+json",
                        "User-Agent": "FastMCP-GitHub-OAuth",
                    },
                )

                # Extract scopes from X-OAuth-Scopes header if available
                oauth_scopes_header = scopes_response.headers.get("x-oauth-scopes", "")
                token_scopes = [
                    scope.strip()
                    for scope in oauth_scopes_header.split(",")
                    if scope.strip()
                ]

                # If no scopes in header, assume basic scopes based on successful user API call
                if not token_scopes:
                    token_scopes = ["user"]  # Basic scope if we can access user info

                # Check required scopes
                if self.required_scopes:
                    token_scopes_set = set(token_scopes)
                    required_scopes_set = set(self.required_scopes)
                    if not required_scopes_set.issubset(token_scopes_set):
                        logger.debug(
                            "GitHub token missing required scopes. Has %d, needs %d",
                            len(token_scopes_set),
                            len(required_scopes_set),
                        )
                        return None

                # Create AccessToken with GitHub user info
                return AccessToken(
                    token=token,
                    client_id=str(user_data.get("id", "unknown")),  # Use GitHub user ID
                    scopes=token_scopes,
                    expires_at=None,  # GitHub tokens don't typically expire
                    claims={
                        "sub": str(user_data["id"]),
                        "login": user_data.get("login"),
                        "name": user_data.get("name"),
                        "email": user_data.get("email"),
                        "avatar_url": user_data.get("avatar_url"),
                        "github_user_data": user_data,
                    },
                )

        except httpx.RequestError as e:
            logger.debug("Failed to verify GitHub token: %s", e)
            return None
        except Exception as e:
            logger.debug("GitHub token verification error: %s", e)
            return None


class GitHubProvider(OAuthProxy):
    """Complete GitHub OAuth provider for FastMCP.

    This provider makes it trivial to add GitHub OAuth protection to any
    FastMCP server. Just provide your GitHub OAuth app credentials and
    a base URL, and you're ready to go.

    Features:
    - Transparent OAuth proxy to GitHub
    - Automatic token validation via GitHub API
    - User information extraction
    - Minimal configuration required

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.github import GitHubProvider

        auth = GitHubProvider(
            client_id="Ov23li...",
            client_secret="abc123...",
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
        issuer_url: AnyHttpUrl | str | NotSetT = NotSet,
        redirect_path: str | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT = NotSet,
        timeout_seconds: int | NotSetT = NotSet,
        allowed_client_redirect_uris: list[str] | NotSetT = NotSet,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | NotSetT = NotSet,
        require_authorization_consent: bool = True,
    ):
        """Initialize GitHub OAuth provider.

        Args:
            client_id: GitHub OAuth app client ID (e.g., "Ov23li...")
            client_secret: GitHub OAuth app client secret
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path configured in GitHub OAuth app (defaults to "/auth/callback")
            required_scopes: Required GitHub scopes (defaults to ["user"])
            timeout_seconds: HTTP request timeout for GitHub API calls
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, a DiskStore will be created in the data directory (derived from `platformdirs`). The
                disk store will be encrypted using a key derived from the JWT Signing Key.
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to GitHub.
                When False, authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or testing environments.
        """

        settings = GitHubProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "base_url": base_url,
                    "issuer_url": issuer_url,
                    "redirect_path": redirect_path,
                    "required_scopes": required_scopes,
                    "timeout_seconds": timeout_seconds,
                    "allowed_client_redirect_uris": allowed_client_redirect_uris,
                    "jwt_signing_key": jwt_signing_key,
                }.items()
                if v is not NotSet
            }
        )

        # Validate required settings
        if not settings.client_id:
            raise ValueError(
                "client_id is required - set via parameter or FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID"
            )
        if not settings.client_secret:
            raise ValueError(
                "client_secret is required - set via parameter or FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET"
            )

        # Apply defaults

        timeout_seconds_final = settings.timeout_seconds or 10
        required_scopes_final = settings.required_scopes or ["user"]
        allowed_client_redirect_uris_final = settings.allowed_client_redirect_uris

        # Create GitHub token verifier
        token_verifier = GitHubTokenVerifier(
            required_scopes=required_scopes_final,
            timeout_seconds=timeout_seconds_final,
        )

        # Extract secret string from SecretStr
        client_secret_str = (
            settings.client_secret.get_secret_value() if settings.client_secret else ""
        )

        # Initialize OAuth proxy with GitHub endpoints
        super().__init__(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id=settings.client_id,
            upstream_client_secret=client_secret_str,
            token_verifier=token_verifier,
            base_url=settings.base_url,
            redirect_path=settings.redirect_path,
            issuer_url=settings.issuer_url
            or settings.base_url,  # Default to base_url if not specified
            allowed_client_redirect_uris=allowed_client_redirect_uris_final,
            client_storage=client_storage,
            jwt_signing_key=settings.jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
        )

        logger.debug(
            "Initialized GitHub OAuth provider for client %s with scopes: %s",
            settings.client_id,
            required_scopes_final,
        )
