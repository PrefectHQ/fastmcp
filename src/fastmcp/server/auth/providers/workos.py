from __future__ import annotations

import httpx
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.registry import register_provider
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class AuthKitProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_AUTHKITPROVIDER_",
        env_file=".env",
        extra="ignore",
    )

    authkit_domain: AnyHttpUrl
    base_url: AnyHttpUrl
    required_scopes: list[str] | None = None


@register_provider("AUTHKIT")
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
        required_scopes: list[str] | None | NotSetT = NotSet,
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
            resource_server_url=self.base_url,
        )

    def get_routes(self) -> list[Route]:
        """Get OAuth routes including AuthKit authorization server metadata forwarding.

        This returns the standard protected resource routes plus an authorization server
        metadata endpoint that forwards AuthKit's OAuth metadata to clients.
        """
        # Get the standard protected resource routes from RemoteAuthProvider
        routes = super().get_routes()

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
