"""Auth0 OAuth providers for FastMCP.

This module provides two Auth0 integrations:

- ``Auth0Provider`` — OAuth proxy for fixed Auth0 application credentials
- ``Auth0MCPProvider`` — resource server for Auth0 Auth for MCP (DCR/CIMD)

Example (OAuth proxy):
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.auth0 import Auth0Provider

    auth = Auth0Provider(
        config_url="https://auth0.config.url",
        client_id="your-auth0-client-id",
        client_secret="your-auth0-client-secret",
        audience="your-auth0-api-audience",
        base_url="http://localhost:8000",
    )

    mcp = FastMCP("My Protected Server", auth=auth)
    ```

Example (Auth for MCP):
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.auth0 import Auth0MCPProvider

    auth = Auth0MCPProvider(
        config_url="https://your-tenant.auth0.com/.well-known/openid-configuration",
        base_url="http://127.0.0.1:8000",
    )

    mcp = FastMCP("My MCP Server", auth=auth)
    ```
"""

from __future__ import annotations

from typing import Any, Literal

import httpx2
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.oidc_proxy import (
    DEFAULT_OIDC_DISCOVERY_TIMEOUT_SECONDS,
    OIDCConfiguration,
    OIDCProxy,
)
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class Auth0Provider(OIDCProxy):
    """An Auth0 provider implementation for FastMCP.

    This provider is a complete Auth0 integration that's ready to use with
    just the configuration URL, client ID, client secret, audience, and base URL.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.auth0 import Auth0Provider

        # Simple Auth0 OAuth protection
        auth = Auth0Provider(
            config_url="https://auth0.config.url",
            client_id="your-auth0-client-id",
            client_secret="your-auth0-client-secret",
            audience="your-auth0-api-audience",
            base_url="http://localhost:8000",
        )

        mcp = FastMCP("My Protected Server", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        config_url: AnyHttpUrl | str,
        client_id: str,
        client_secret: str,
        audience: str,
        timeout_seconds: int | None = DEFAULT_OIDC_DISCOVERY_TIMEOUT_SECONDS,
        base_url: AnyHttpUrl | str,
        resource_base_url: AnyHttpUrl | str | None = None,
        issuer_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
        redirect_path: str | None = None,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool | Literal["remember", "external"] = True,
        consent_csp_policy: str | None = None,
        forward_resource: bool = True,
        fallback_refresh_token_expiry_seconds: int | None = None,
        fastmcp_access_token_expiry_seconds: int | None = None,
        token_expiry_threshold_seconds: int = 0,
    ) -> None:
        """Initialize Auth0 OAuth provider.

        Args:
            config_url: Auth0 config URL
            client_id: Auth0 application client id
            client_secret: Auth0 application client secret
            audience: Auth0 API audience
            timeout_seconds: Timeout, in seconds, for the OIDC discovery request
                made during construction. Defaults to 10 seconds so a slow or
                unreachable issuer cannot block server startup indefinitely. Pass
                None to fall back to the HTTP client's own default timeout.
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            resource_base_url: Optional public base URL for the protected resource metadata
                and token audience. Defaults to ``base_url``.
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            required_scopes: Required Auth0 scopes (defaults to ["openid"])
            redirect_path: Redirect path configured in Auth0 application
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, an encrypted file store will be created in the data directory
                (derived from `platformdirs`).
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to Auth0.
                When False, authorization proceeds directly without user confirmation.
                When "external", the built-in consent screen is skipped but no warning is
                logged, indicating that consent is handled externally (e.g. by the upstream IdP).
                SECURITY WARNING: Only set to False for local development or testing environments.
            fallback_refresh_token_expiry_seconds: Lifetime for the FastMCP-issued
                refresh token when the upstream provider omits `refresh_expires_in`
                (e.g. Cognito, GitHub, many OIDC IdPs). Defaults to 1 year. The upstream
                refresh remains the source of truth. See `OAuthProxy` for details.
            fastmcp_access_token_expiry_seconds: Lifetime for the FastMCP-issued access
                token, decoupling it from the upstream provider's `expires_in`. Defaults
                to None (mirror the upstream lifetime). Set this for bridges whose
                upstream issues short-lived access tokens that some MCP clients can't
                refresh gracefully (e.g. `mcp-remote`). See `OAuthProxy` for details.
            token_expiry_threshold_seconds: Number of seconds before actual expiry to
                treat a token as expired, refreshing early to avoid races. Defaults to 0.
        """
        # Parse scopes if provided as string
        auth0_required_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else ["openid"]
        )

        super().__init__(
            config_url=config_url,
            client_id=client_id,
            client_secret=client_secret,
            audience=audience,
            timeout_seconds=timeout_seconds,
            base_url=base_url,
            resource_base_url=resource_base_url,
            issuer_url=issuer_url,
            redirect_path=redirect_path,
            required_scopes=auth0_required_scopes,
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
            consent_csp_policy=consent_csp_policy,
            forward_resource=forward_resource,
            fallback_refresh_token_expiry_seconds=fallback_refresh_token_expiry_seconds,
            fastmcp_access_token_expiry_seconds=fastmcp_access_token_expiry_seconds,
            token_expiry_threshold_seconds=token_expiry_threshold_seconds,
        )

        logger.debug(
            "Initialized Auth0 OAuth provider for client %s with scopes: %s",
            client_id,
            auth0_required_scopes,
        )


class Auth0JWTVerifier(JWTVerifier):
    """JWT verifier for Auth0 MCP access tokens.

    Auth0's ``rfc9068_profile_authz`` token dialect exposes API permissions in
    the ``permissions`` claim. Standard OAuth ``scope``/``scp`` claims are checked
    first; ``permissions`` is included when present.
    """

    def _extract_scopes(self, claims: dict[str, Any]) -> list[str]:
        scopes = super()._extract_scopes(claims)
        permissions = claims.get("permissions")
        if isinstance(permissions, str):
            return scopes + permissions.split()
        if isinstance(permissions, list):
            return scopes + [str(permission) for permission in permissions]
        return scopes


class Auth0MCPProvider(RemoteAuthProvider):
    """Auth0 resource server provider for Auth for MCP (DCR/CIMD).

    FastMCP validates access tokens issued by Auth0 while Auth0 handles OAuth,
    dynamic client registration, and CIMD approval in the tenant dashboard.

    Enable the Resource Parameter Compatibility Profile in Auth0 and create an
    API whose identifier matches this server's resource URL (logged at startup).

    Example:
        ```python
        from fastmcp.server.auth.providers.auth0 import Auth0MCPProvider

        auth = Auth0MCPProvider(
            config_url="https://your-tenant.auth0.com/.well-known/openid-configuration",
            base_url="http://127.0.0.1:8000",
        )
        ```
    """

    def __init__(
        self,
        *,
        config_url: AnyHttpUrl | str,
        base_url: AnyHttpUrl | str,
        resource_base_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
        scopes_supported: list[str] | None = None,
        resource_name: str | None = None,
        resource_documentation: AnyHttpUrl | None = None,
        token_verifier: TokenVerifier | None = None,
        timeout_seconds: int | None = DEFAULT_OIDC_DISCOVERY_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize Auth0 MCP resource server provider.

        Args:
            config_url: Auth0 OIDC discovery URL
            base_url: Public URL of this FastMCP server
            resource_base_url: Optional public base URL for protected resource metadata
            required_scopes: Scopes or permissions required on every token
            scopes_supported: Scopes advertised in OAuth metadata
            resource_name: Optional protected resource name
            resource_documentation: Optional protected resource documentation URL
            token_verifier: Optional custom verifier (skips audience auto-binding)
            timeout_seconds: OIDC discovery timeout during construction
        """
        oidc_config = OIDCConfiguration.get_oidc_configuration(
            AnyHttpUrl(str(config_url)),
            strict=None,
            timeout_seconds=timeout_seconds,
        )
        self.issuer = str(oidc_config.issuer).rstrip("/")
        self.base_url = AnyHttpUrl(str(base_url).rstrip("/"))

        parsed_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else None
        )

        self._auto_bind_audience = token_verifier is None
        if token_verifier is None:
            token_verifier = Auth0JWTVerifier(
                jwks_uri=str(oidc_config.jwks_uri),
                issuer=str(oidc_config.issuer),
                algorithm="RS256",
                required_scopes=parsed_scopes,
            )

        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(self.issuer)],
            base_url=self.base_url,
            resource_base_url=resource_base_url,
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
        )

    def set_mcp_path(self, mcp_path: str | None) -> None:
        """Bind the default verifier's audience to this server's resource URL."""
        super().set_mcp_path(mcp_path)
        if (
            self._auto_bind_audience
            and self._resource_url is not None
            and isinstance(self.token_verifier, JWTVerifier)
        ):
            resource_url = str(self._resource_url)
            self.token_verifier.audience = resource_url
            logger.info(
                "Auth0 tokens will be validated against aud=%s. "
                "Set your Auth0 API identifier to this URL and enable the "
                "Resource Parameter Compatibility Profile.",
                resource_url,
            )

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Protected resource routes plus Auth0 authorization server metadata."""
        routes = super().get_routes(mcp_path)
        metadata_url = f"{self.issuer}/.well-known/oauth-authorization-server"

        async def oauth_authorization_server_metadata(request):
            try:
                async with httpx2.AsyncClient() as client:
                    response = await client.get(metadata_url)
                    response.raise_for_status()
                    return JSONResponse(response.json())
            except Exception as e:
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Failed to fetch Auth0 metadata: {e}",
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
