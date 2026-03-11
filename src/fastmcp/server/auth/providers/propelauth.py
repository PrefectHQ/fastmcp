"""PropelAuth authentication provider for FastMCP.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.propelauth import PropelAuthProvider

    auth = PropelAuthProvider(
        auth_url="https://auth.yourdomain.com",
        introspection_client_id="your-client-id",
        introspection_client_secret="your-client-secret",
        base_url="https://your-fastmcp-server.com",
        required_scopes=["read:user_data"],
    )

    mcp = FastMCP("My App", auth=auth)
    ```
"""

from __future__ import annotations

from typing import TypedDict

import httpx
from pydantic import AnyHttpUrl, SecretStr
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import AccessToken, RemoteAuthProvider
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class PropelAuthTokenIntrospectionOverrides(TypedDict, total=False):
    timeout_seconds: int
    cache_ttl_seconds: int | None
    max_cache_size: int | None
    http_client: httpx.AsyncClient | None


class PropelAuthProvider(RemoteAuthProvider):
    """PropelAuth resource server provider using OAuth 2.1 token introspection.

    This provider validates access tokens via PropelAuth's introspection endpoint
    and forwards authorization server metadata for OAuth discovery.

    Setup:
        1. Enable MCP authentication in the PropelAuth Dashboard
        2. Configure scopes on the MCP page
        3. Select which redirect URIs to enable by picking which clients you support
        4. Generate introspection credentials (Client ID + Client Secret)

    For detailed setup instructions, see:
    https://docs.propelauth.com/mcp-authentication/overview

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.propelauth import PropelAuthProvider

        auth = PropelAuthProvider(
            auth_url="https://auth.yourdomain.com",
            introspection_client_id="your-client-id",
            introspection_client_secret="your-client-secret",
            base_url="https://your-fastmcp-server.com",
            required_scopes=["read:user_data"],
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        auth_url: AnyHttpUrl | str,
        introspection_client_id: str,
        introspection_client_secret: str | SecretStr,
        base_url: AnyHttpUrl | str,
        required_scopes: list[str] | None = None,
        scopes_supported: list[str] | None = None,
        resource_name: str | None = None,
        resource_documentation: AnyHttpUrl | None = None,
        resource: AnyHttpUrl | str | None = None,
        token_introspection_overrides: (
            PropelAuthTokenIntrospectionOverrides | None
        ) = None,
    ):
        """Initialize PropelAuth provider.

        Args:
            auth_url: Your PropelAuth Auth URL (from the Backend Integration page)
            introspection_client_id: Introspection Client ID from the PropelAuth Dashboard
            introspection_client_secret: Introspection Client Secret from the PropelAuth Dashboard
            base_url: Public URL of this FastMCP server
            required_scopes: Optional list of scopes that must be present in tokens
            scopes_supported: Optional list of scopes to advertise in OAuth metadata.
                If None, uses required_scopes. Use this when the scopes clients should
                request differ from the scopes enforced on tokens.
            resource_name: Optional name for the protected resource metadata.
            resource_documentation: Optional documentation URL for the protected resource.
            resource: Optional resource URI (RFC 8707) identifying this MCP server.
                Use this when multiple MCP servers share the same PropelAuth
                authorization server (e.g. ``resource="https://api.example.com/mcp"``),
                so only tokens intended for this MCP server are accepted.
            token_introspection_overrides: Optional overrides for the underlying
                IntrospectionTokenVerifier (timeout, caching, http_client)
        """
        normalized_auth_url = str(auth_url).rstrip("/")
        introspection_url = f"{normalized_auth_url}/oauth/2.1/introspect"
        authorization_server_url = AnyHttpUrl(f"{normalized_auth_url}/oauth/2.1")

        if resource is None:
            self._resource = None
            logger.debug(
                "PropelAuthProvider: no resource configured, audience checking disabled"
            )
        else:
            self._resource = str(resource)

        token_verifier = self._create_token_verifier(
            introspection_url=introspection_url,
            client_id=introspection_client_id,
            client_secret=introspection_client_secret,
            required_scopes=required_scopes,
            introspection_overrides=token_introspection_overrides,
        )

        self._normalized_auth_url = normalized_auth_url
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[authorization_server_url],
            base_url=base_url,
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
        )

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get routes for this provider.

        Includes the standard routes from the RemoteAuthProvider (protected resource metadata routes (RFC 9728)),
        and creates an authorization server metadata route that forwards to PropelAuth's route

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
                This is used to advertise the resource URL in metadata.
        """
        routes = super().get_routes(mcp_path)

        async def oauth_authorization_server_metadata(request):
            """Forward PropelAuth OAuth authorization server metadata"""
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self._normalized_auth_url}/.well-known/oauth-authorization-server/oauth/2.1"
                    )
                    response.raise_for_status()
                    metadata = response.json()
                    return JSONResponse(metadata)
            except Exception as e:
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Failed to fetch PropelAuth metadata: {e}",
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

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify token and check the ``aud`` claim against the configured resource."""
        result = await super().verify_token(token)
        if result is None or self._resource is None:
            return result

        aud = result.claims.get("aud")
        if aud != self._resource:
            logger.debug(
                "PropelAuthProvider: token audience %r does not match resource %s",
                aud,
                self._resource,
            )
            return None

        return result

    def _create_token_verifier(
        self,
        introspection_url: str,
        client_id: str,
        client_secret: str | SecretStr,
        required_scopes: list[str] | None,
        introspection_overrides: PropelAuthTokenIntrospectionOverrides | None,
    ) -> IntrospectionTokenVerifier:
        # Being defensive here, check for only the fields we are expecting
        safe_overrides: PropelAuthTokenIntrospectionOverrides = {}
        if introspection_overrides is not None:
            if "timeout_seconds" in introspection_overrides:
                safe_overrides["timeout_seconds"] = introspection_overrides[
                    "timeout_seconds"
                ]
            if "cache_ttl_seconds" in introspection_overrides:
                safe_overrides["cache_ttl_seconds"] = introspection_overrides[
                    "cache_ttl_seconds"
                ]
            if "max_cache_size" in introspection_overrides:
                safe_overrides["max_cache_size"] = introspection_overrides[
                    "max_cache_size"
                ]
            if "http_client" in introspection_overrides:
                safe_overrides["http_client"] = introspection_overrides["http_client"]

        return IntrospectionTokenVerifier(
            introspection_url=introspection_url,
            client_id=client_id,
            client_secret=client_secret,
            required_scopes=required_scopes,
            **safe_overrides,
        )
