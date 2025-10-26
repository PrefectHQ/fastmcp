"""WorkOS authentication providers for FastMCP.

This module provides two WorkOS authentication strategies:

1. WorkOSProvider - OAuth proxy for WorkOS Connect applications (non-DCR)
2. AuthKitProvider - DCR-compliant provider for WorkOS AuthKit

Choose based on your WorkOS setup and authentication requirements.
"""

from __future__ import annotations

import httpx
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import AccessToken, RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class WorkOSProviderSettings(BaseSettings):
    """Settings for WorkOS OAuth provider."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_WORKOS_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    client_id: str | None = None
    client_secret: SecretStr | None = None
    authkit_domain: str | None = None  # e.g., "https://your-app.authkit.app"
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


class WorkOSTokenVerifier(TokenVerifier):
    """Token verifier for WorkOS OAuth tokens.

    WorkOS AuthKit tokens are opaque, so we verify them by calling
    the /oauth2/userinfo endpoint to check validity and get user info.
    """

    def __init__(
        self,
        *,
        authkit_domain: str,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
    ):
        """Initialize the WorkOS token verifier.

        Args:
            authkit_domain: WorkOS AuthKit domain (e.g., "https://your-app.authkit.app")
            required_scopes: Required OAuth scopes
            timeout_seconds: HTTP request timeout
        """
        super().__init__(required_scopes=required_scopes)
        self.authkit_domain = authkit_domain.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify WorkOS OAuth token by calling userinfo endpoint."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                # Use WorkOS AuthKit userinfo endpoint to validate token
                response = await client.get(
                    f"{self.authkit_domain}/oauth2/userinfo",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "FastMCP-WorkOS-OAuth",
                    },
                )

                if response.status_code != 200:
                    logger.debug(
                        "WorkOS token verification failed: %d - %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return None

                user_data = response.json()

                # Create AccessToken with WorkOS user info
                return AccessToken(
                    token=token,
                    client_id=str(user_data.get("sub", "unknown")),
                    scopes=self.required_scopes or [],
                    expires_at=None,  # Will be set from token introspection if needed
                    claims={
                        "sub": user_data.get("sub"),
                        "email": user_data.get("email"),
                        "email_verified": user_data.get("email_verified"),
                        "name": user_data.get("name"),
                        "given_name": user_data.get("given_name"),
                        "family_name": user_data.get("family_name"),
                    },
                )

        except httpx.RequestError as e:
            logger.debug("Failed to verify WorkOS token: %s", e)
            return None
        except Exception as e:
            logger.debug("WorkOS token verification error: %s", e)
            return None


class WorkOSProvider(OAuthProxy):
    """Complete WorkOS OAuth provider for FastMCP.

    This provider implements WorkOS AuthKit OAuth using the OAuth Proxy pattern.
    It provides OAuth2 authentication for users through WorkOS Connect applications.

    Features:
    - Transparent OAuth proxy to WorkOS AuthKit
    - Automatic token validation via userinfo endpoint
    - User information extraction from ID tokens
    - Support for standard OAuth scopes (openid, profile, email)

    Setup Requirements:
    1. Create a WorkOS Connect application in your dashboard
    2. Note your AuthKit domain (e.g., "https://your-app.authkit.app")
    3. Configure redirect URI as: http://localhost:8000/auth/callback
    4. Note your Client ID and Client Secret

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.workos import WorkOSProvider

        auth = WorkOSProvider(
            client_id="client_123",
            client_secret="sk_test_456",
            authkit_domain="https://your-app.authkit.app",
            base_url="http://localhost:8000"
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        client_id: str | NotSetT = NotSet,
        client_secret: str | NotSetT = NotSet,
        authkit_domain: str | NotSetT = NotSet,
        base_url: AnyHttpUrl | str | NotSetT = NotSet,
        issuer_url: AnyHttpUrl | str | NotSetT = NotSet,
        redirect_path: str | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT | None = NotSet,
        timeout_seconds: int | NotSetT = NotSet,
        allowed_client_redirect_uris: list[str] | NotSetT = NotSet,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | NotSetT = NotSet,
        require_authorization_consent: bool = True,
    ):
        """Initialize WorkOS OAuth provider.

        Args:
            client_id: WorkOS client ID
            client_secret: WorkOS client secret
            authkit_domain: Your WorkOS AuthKit domain (e.g., "https://your-app.authkit.app")
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path configured in WorkOS (defaults to "/auth/callback")
            required_scopes: Required OAuth scopes (no default)
            timeout_seconds: HTTP request timeout for WorkOS API calls
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, a DiskStore will be created in the data directory (derived from `platformdirs`). The
                disk store will be encrypted using a key derived from the JWT Signing Key.
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to WorkOS.
                When False, authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or testing environments.
        """

        settings = WorkOSProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "authkit_domain": authkit_domain,
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
                "client_id is required - set via parameter or FASTMCP_SERVER_AUTH_WORKOS_CLIENT_ID"
            )
        if not settings.client_secret:
            raise ValueError(
                "client_secret is required - set via parameter or FASTMCP_SERVER_AUTH_WORKOS_CLIENT_SECRET"
            )
        if not settings.authkit_domain:
            raise ValueError(
                "authkit_domain is required - set via parameter or FASTMCP_SERVER_AUTH_WORKOS_AUTHKIT_DOMAIN"
            )

        # Apply defaults and ensure authkit_domain is a full URL
        authkit_domain_str = settings.authkit_domain
        if not authkit_domain_str.startswith(("http://", "https://")):
            authkit_domain_str = f"https://{authkit_domain_str}"
        authkit_domain_final = authkit_domain_str.rstrip("/")
        timeout_seconds_final = settings.timeout_seconds or 10
        scopes_final = settings.required_scopes or []
        allowed_client_redirect_uris_final = settings.allowed_client_redirect_uris

        # Extract secret string from SecretStr
        client_secret_str = (
            settings.client_secret.get_secret_value() if settings.client_secret else ""
        )

        # Create WorkOS token verifier
        token_verifier = WorkOSTokenVerifier(
            authkit_domain=authkit_domain_final,
            required_scopes=scopes_final,
            timeout_seconds=timeout_seconds_final,
        )

        # Initialize OAuth proxy with WorkOS AuthKit endpoints
        super().__init__(
            upstream_authorization_endpoint=f"{authkit_domain_final}/oauth2/authorize",
            upstream_token_endpoint=f"{authkit_domain_final}/oauth2/token",
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
            "Initialized WorkOS OAuth provider for client %s with AuthKit domain %s",
            settings.client_id,
            authkit_domain_final,
        )


class AuthKitProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_AUTHKITPROVIDER_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    authkit_domain: AnyHttpUrl
    base_url: AnyHttpUrl
    required_scopes: list[str] | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class AuthKitProvider(RemoteAuthProvider):
    """AuthKit metadata provider for DCR (Dynamic Client Registration).

    This provider implements AuthKit integration using metadata forwarding
    instead of OAuth proxying. This is the recommended approach for WorkOS DCR
    as it allows WorkOS to handle the OAuth flow directly while FastMCP acts
    as a resource server.

    IMPORTANT SETUP REQUIREMENTS:

    1. Enable Dynamic Client Registration in WorkOS Dashboard:
       - Go to Applications → Configuration
       - Toggle "Dynamic Client Registration" to enabled

    2. Configure your FastMCP server URL as a callback:
       - Add your server URL to the Redirects tab in WorkOS dashboard
       - Example: https://your-fastmcp-server.com/oauth2/callback

    For detailed setup instructions, see:
    https://workos.com/docs/authkit/mcp/integrating/token-verification

    Example:
        ```python
        from fastmcp.server.auth.providers.workos import AuthKitProvider

        # Create AuthKit metadata provider (JWT verifier created automatically)
        workos_auth = AuthKitProvider(
            authkit_domain="https://your-workos-domain.authkit.app",
            base_url="https://your-fastmcp-server.com",
        )

        # Use with FastMCP
        mcp = FastMCP("My App", auth=workos_auth)
        ```
    """

    def __init__(
        self,
        *,
        authkit_domain: AnyHttpUrl | str | NotSetT = NotSet,
        base_url: AnyHttpUrl | str | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT | None = NotSet,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize AuthKit metadata provider.

        Args:
            authkit_domain: Your AuthKit domain (e.g., "https://your-app.authkit.app")
            base_url: Public URL of this FastMCP server
            required_scopes: Optional list of scopes to require for all requests
            token_verifier: Optional token verifier. If None, creates JWT verifier for AuthKit
        """
        settings = AuthKitProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "authkit_domain": authkit_domain,
                    "base_url": base_url,
                    "required_scopes": required_scopes,
                }.items()
                if v is not NotSet
            }
        )

        self.authkit_domain = str(settings.authkit_domain).rstrip("/")
        self.base_url = str(settings.base_url).rstrip("/")

        # Create default JWT verifier if none provided
        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.authkit_domain}/oauth2/jwks",
                issuer=self.authkit_domain,
                algorithm="RS256",
                required_scopes=settings.required_scopes,
            )

        # Initialize RemoteAuthProvider with AuthKit as the authorization server
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(self.authkit_domain)],
            base_url=self.base_url,
        )

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get OAuth routes including AuthKit authorization server metadata forwarding.

        This returns the standard protected resource routes plus an authorization server
        metadata endpoint that forwards AuthKit's OAuth metadata to clients.

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
                This is used to advertise the resource URL in metadata.
        """
        # Get the standard protected resource routes from RemoteAuthProvider
        routes = super().get_routes(mcp_path)

        async def oauth_authorization_server_metadata(request):
            """Forward AuthKit OAuth authorization server metadata with FastMCP customizations."""
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.authkit_domain}/.well-known/oauth-authorization-server"
                    )
                    response.raise_for_status()
                    metadata = response.json()
                    return JSONResponse(metadata)
            except Exception as e:
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Failed to fetch AuthKit metadata: {e}",
                    },
                    status_code=500,
                )

        # Add AuthKit authorization server metadata forwarding
        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=oauth_authorization_server_metadata,
                methods=["GET"],
            )
        )

        return routes
