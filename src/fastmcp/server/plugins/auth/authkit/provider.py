"""WorkOS AuthKit provider."""

from __future__ import annotations

import httpx
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class AuthKitProvider(RemoteAuthProvider):
    """AuthKit metadata provider for DCR (Dynamic Client Registration).

    This provider implements AuthKit integration using metadata forwarding
    instead of OAuth proxying. This is the recommended approach for WorkOS DCR
    as it allows WorkOS to handle the OAuth flow directly while FastMCP acts
    as a resource server.

    IMPORTANT SETUP REQUIREMENTS:

    1. Enable Dynamic Client Registration in WorkOS Dashboard:
       - Go to Applications -> Configuration
       - Toggle "Dynamic Client Registration" to enabled

    2. Configure your FastMCP server URL as a callback:
       - Add your server URL to the Redirects tab in WorkOS dashboard
       - Example: https://your-fastmcp-server.com/oauth2/callback

    For detailed setup instructions, see:
    https://workos.com/docs/authkit/mcp/integrating/token-verification

    Token audience is bound to this server automatically: when the MCP
    mount path becomes known (typically at ``http_app()`` construction),
    ``JWTVerifier.audience`` is set to the resource URL advertised in
    ``.well-known/oauth-protected-resource``. Enable Resource Indicators
    (RFC 8707) in your WorkOS Dashboard and list that same URL — AuthKit
    will then mint tokens with the matching ``aud`` claim.

    Example:
        ```python
        from fastmcp.server.plugins.auth.authkit.provider import AuthKitProvider

        workos_auth = AuthKitProvider(
            authkit_domain="https://your-workos-domain.authkit.app",
            base_url="https://your-fastmcp-server.com",
        )

        mcp = FastMCP("My App", auth=workos_auth)
        ```
    """

    def __init__(
        self,
        *,
        authkit_domain: AnyHttpUrl | str,
        base_url: AnyHttpUrl | str,
        resource_base_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
        scopes_supported: list[str] | None = None,
        resource_name: str | None = None,
        resource_documentation: AnyHttpUrl | None = None,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize AuthKit metadata provider.

        Args:
            authkit_domain: Your AuthKit domain (e.g., "https://your-app.authkit.app")
            base_url: Public URL of this FastMCP server
            resource_base_url: Optional public base URL for the protected resource.
                When provided, this URL is advertised in protected resource metadata
                instead of ``base_url``. Useful when OAuth callbacks and the protected
                MCP resource live under different public URLs.
            required_scopes: Optional list of scopes to require for all requests
            scopes_supported: Optional list of scopes to advertise in OAuth metadata.
                If None, uses required_scopes. Use this when the scopes clients should
                request differ from the scopes enforced on tokens.
            resource_name: Optional name for the protected resource metadata.
            resource_documentation: Optional documentation URL for the protected resource.
            token_verifier: Optional token verifier. If provided, it is used as-is and
                audience auto-wiring is skipped — the caller is responsible for setting
                an appropriate ``audience``. If None (default), a ``JWTVerifier`` is
                created with audience bound to this server's resource URL.
        """
        self.authkit_domain = str(authkit_domain).rstrip("/")
        self.base_url = AnyHttpUrl(str(base_url).rstrip("/"))

        parsed_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else None
        )

        # When no custom verifier is provided, we own the JWTVerifier and can
        # bind its audience to our resource URL once set_mcp_path() is called.
        self._auto_bind_audience = token_verifier is None
        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.authkit_domain}/oauth2/jwks",
                issuer=self.authkit_domain,
                algorithm="RS256",
                required_scopes=parsed_scopes,
            )

        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(self.authkit_domain)],
            base_url=self.base_url,
            resource_base_url=resource_base_url,
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
        )

    def set_mcp_path(self, mcp_path: str | None) -> None:
        """Bind the default verifier's audience to this server's resource URL.

        AuthKit with Resource Indicators (RFC 8707) mints tokens whose ``aud``
        claim equals the resource URL the client requested — which is the URL
        we advertise in ``.well-known/oauth-protected-resource``. Binding the
        audience here keeps validation in lock-step with what clients are sent.
        """
        super().set_mcp_path(mcp_path)
        if (
            self._auto_bind_audience
            and self._resource_url is not None
            and isinstance(self.token_verifier, JWTVerifier)
        ):
            resource_url = str(self._resource_url)
            self.token_verifier.audience = resource_url
            logger.info(
                "AuthKit tokens will be validated against aud=%s. "
                "Configure this URL as a Resource Indicator in the WorkOS Dashboard.",
                resource_url,
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

        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=oauth_authorization_server_metadata,
                methods=["GET"],
            )
        )

        return routes


__all__ = ["AuthKitProvider"]
