from __future__ import annotations

import httpx
from mcp.server.auth.provider import (
    AccessToken,
)
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from fastmcp.server.auth.auth import AuthProvider, TokenVerifier
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
class AuthKitProvider(AuthProvider):
    """WorkOS AuthKit metadata provider for DCR (Dynamic Client Registration).

    This provider implements WorkOS AuthKit integration using metadata forwarding
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

        # Create WorkOS metadata provider (JWT verifier created automatically)
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
        """Initialize WorkOS metadata provider.

        Args:
            authkit_domain: Your WorkOS AuthKit domain (e.g., "https://your-app.authkit.app")
            base_url: Public URL of this FastMCP server
            required_scopes: Optional list of scopes to require for all requests
            token_verifier: Optional token verifier. If None, creates JWT verifier for WorkOS
        """
        super().__init__()

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

        self.token_verifier = token_verifier

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a WorkOS token using the configured token verifier."""
        return await self.token_verifier.verify_token(token)

    def customize_auth_routes(self, routes: list[BaseRoute]) -> list[BaseRoute]:
        """Add AuthKit metadata endpoints.

        This adds:
        - /.well-known/oauth-authorization-server (forwards AuthKit metadata)
        - /.well-known/oauth-protected-resource (returns FastMCP resource info)
        """

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

        async def oauth_protected_resource_metadata(request):
            """Return FastMCP resource server metadata."""
            return JSONResponse(
                {
                    "resource": self.base_url,
                    "authorization_servers": [self.authkit_domain],
                    "bearer_methods_supported": ["header"],
                }
            )

        routes.extend(
            [
                Route(
                    "/.well-known/oauth-authorization-server",
                    endpoint=oauth_authorization_server_metadata,
                    methods=["GET"],
                ),
                Route(
                    "/.well-known/oauth-protected-resource",
                    endpoint=oauth_protected_resource_metadata,
                    methods=["GET"],
                ),
            ]
        )

        return routes
