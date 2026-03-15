"""Keycloak authentication provider for FastMCP.

This module provides KeycloakAuthProvider - a complete authentication solution that integrates
with Keycloak's OAuth 2.1 and OpenID Connect services, supporting Dynamic Client Registration (DCR)
for seamless MCP client authentication.
"""

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


class KeycloakAuthProvider(RemoteAuthProvider):
    """Keycloak authentication provider with Dynamic Client Registration (DCR) support.

    This provider integrates FastMCP with Keycloak using a **minimal proxy architecture** that
    solves a specific MCP compatibility issue. The proxy only intercepts DCR responses to fix
    a single field - all other OAuth operations go directly to Keycloak.

    ## Why a Minimal Proxy is Needed (for older Keycloak versions)

    **Note:** Keycloak fixed this issue in PR #45309 (merged January 12, 2026). Once you upgrade
    to a Keycloak version that includes this fix, the proxy workaround will no longer be necessary.

    Older Keycloak versions have a known limitation with Dynamic Client Registration: they ignore
    the client's requested `token_endpoint_auth_method` parameter and always return
    `client_secret_basic`, even when clients explicitly request `client_secret_post` (which MCP
    requires per RFC 9110).

    This minimal proxy works around this by:
    1. Advertising itself as the authorization server to MCP clients
    2. Forwarding Keycloak's OAuth metadata with a custom registration endpoint
    3. Intercepting DCR responses from Keycloak and fixing only the `token_endpoint_auth_method` field

    **What the minimal proxy does NOT intercept:**
    - Authorization flows (users authenticate directly with Keycloak)
    - Token issuance (tokens come directly from Keycloak)
    - Token validation (JWT signatures verified against Keycloak's keys)

    **Reference:** https://github.com/keycloak/keycloak/pull/45309

    ## Setup Requirements

    1. Configure Keycloak realm with Dynamic Client Registration enabled
    2. Import the FastMCP realm configuration file (recommended) or manually configure:
       - Client Registration Policies with default scopes
       - Trusted hosts for secure client registration
       - Test user credentials

    For detailed setup instructions, see:
    https://gofastmcp.com/integrations/keycloak

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

        # Create Keycloak provider (JWT verifier created automatically)
        keycloak_auth = KeycloakAuthProvider(
            realm_url="http://localhost:8080/realms/fastmcp",
            base_url="http://localhost:8000",
            required_scopes=["openid", "profile"],
            # audience="http://localhost:8000",  # Recommended for production
        )

        # Use with FastMCP
        mcp = FastMCP("My App", auth=keycloak_auth)
        ```
    """

    def __init__(
        self,
        *,
        realm_url: AnyHttpUrl | str,
        base_url: AnyHttpUrl | str,
        required_scopes: list[str] | str | None = None,
        audience: str | list[str] | None = None,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize Keycloak metadata provider.

        Args:
            realm_url: Your Keycloak realm URL (e.g., "https://keycloak.example.com/realms/myrealm")
            base_url: Public URL of this FastMCP server
            required_scopes: Optional list of scopes to require for all requests.
                Can be a list of strings or a comma/space-separated string.
            audience: Optional audience(s) for JWT validation. If not specified and no custom
                verifier is provided, audience validation is disabled. For production use,
                it's recommended to set this to your resource server identifier or base_url.
            token_verifier: Optional token verifier. If None, creates JWT verifier for Keycloak
        """
        # Normalize URLs
        if isinstance(base_url, str):
            base_url = AnyHttpUrl(base_url)
        self.base_url = AnyHttpUrl(str(base_url).rstrip("/"))
        self.realm_url = str(realm_url).rstrip("/")

        # Parse scopes if provided as string
        parsed_scopes = parse_scopes(required_scopes) if required_scopes else None

        # Create default JWT verifier if none provided
        if token_verifier is None:
            # Keycloak uses specific URL patterns (not the standard .well-known paths)
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.realm_url}/protocol/openid-connect/certs",
                issuer=self.realm_url,
                algorithm="RS256",
                required_scopes=parsed_scopes,
                audience=audience,
            )

        # Initialize RemoteAuthProvider with FastMCP as the authorization server
        # We advertise ourselves as the auth server because we provide the
        # authorization server metadata endpoint that forwards from Keycloak
        # with our /register DCR proxy endpoint.
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[self.base_url],
            base_url=self.base_url,
        )

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get OAuth routes including Keycloak metadata forwarding and minimal DCR proxy.

        Adds two routes to the parent class's protected resource metadata:
        1. `/.well-known/oauth-authorization-server` - Forwards Keycloak's OAuth metadata
           with the registration endpoint rewritten to point to our minimal DCR proxy
        2. `/register` - Minimal DCR proxy that forwards requests to Keycloak and fixes
           only the `token_endpoint_auth_method` field in responses

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
        """
        # Get the standard protected resource routes from RemoteAuthProvider
        routes = super().get_routes(mcp_path)

        async def oauth_authorization_server_metadata(request):
            """Forward Keycloak's OAuth metadata with registration endpoint pointing to our minimal DCR proxy."""
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.realm_url}/.well-known/oauth-authorization-server"
                    )
                    response.raise_for_status()
                    metadata = response.json()

                    # Override registration_endpoint to use our minimal DCR proxy
                    base_url = str(self.base_url).rstrip("/")
                    metadata["registration_endpoint"] = f"{base_url}/register"

                    return JSONResponse(metadata)
            except Exception as e:
                logger.error(f"Failed to fetch Keycloak metadata: {e}")
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Failed to fetch Keycloak metadata: {e}",
                    },
                    status_code=500,
                )

        # Add Keycloak authorization server metadata forwarding
        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=oauth_authorization_server_metadata,
                methods=["GET"],
            )
        )

        async def register_client_fix_auth_method(request):
            """Minimal DCR proxy that fixes token_endpoint_auth_method in Keycloak's client registration response.

            Forwards registration requests to Keycloak's DCR endpoint and modifies only the
            token_endpoint_auth_method field in the response, changing "client_secret_basic"
            to "client_secret_post" for MCP compatibility. All other fields are passed through
            unchanged.

            Note: This workaround is only needed for Keycloak versions prior to the fix in PR #45309
            (merged January 12, 2026). Future Keycloak releases will respect the requested method.
            """
            try:
                body = await request.body()

                # Forward to Keycloak's DCR endpoint
                async with httpx.AsyncClient(timeout=10.0) as client:
                    forward_headers = {
                        key: value
                        for key, value in request.headers.items()
                        if key.lower()
                        not in {"host", "content-length", "transfer-encoding"}
                    }
                    forward_headers["Content-Type"] = "application/json"

                    # Keycloak's standard DCR endpoint pattern
                    registration_endpoint = (
                        f"{self.realm_url}/clients-registrations/openid-connect"
                    )
                    response = await client.post(
                        registration_endpoint,
                        content=body,
                        headers=forward_headers,
                    )

                    if response.status_code != 201:
                        return JSONResponse(
                            response.json()
                            if response.headers.get("content-type", "").startswith(
                                "application/json"
                            )
                            else {"error": "registration_failed"},
                            status_code=response.status_code,
                        )

                    # Fix token_endpoint_auth_method for MCP compatibility
                    client_info = response.json()
                    original_auth_method = client_info.get("token_endpoint_auth_method")
                    logger.debug(
                        f"Received token_endpoint_auth_method from Keycloak: {original_auth_method}"
                    )

                    if original_auth_method == "client_secret_basic":
                        logger.debug(
                            "Fixing token_endpoint_auth_method: client_secret_basic -> client_secret_post"
                        )
                        client_info["token_endpoint_auth_method"] = "client_secret_post"

                    logger.debug(
                        f"Returning token_endpoint_auth_method to client: {client_info.get('token_endpoint_auth_method')}"
                    )
                    return JSONResponse(client_info, status_code=201)

            except Exception as e:
                logger.error(f"DCR proxy error: {e}")
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Client registration failed: {e}",
                    },
                    status_code=500,
                )

        # Add minimal DCR proxy
        routes.append(
            Route(
                "/register",
                endpoint=register_client_fix_auth_method,
                methods=["POST"],
            )
        )

        return routes
