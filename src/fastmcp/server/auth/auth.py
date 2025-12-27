from __future__ import annotations

import json
from typing import Any, cast
from urllib.parse import urlparse

from mcp.server.auth.handlers.token import TokenErrorResponse
from mcp.server.auth.handlers.token import TokenHandler as _SDKTokenHandler
from mcp.server.auth.json_response import PydanticJSONResponse
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken as _SDKAccessToken,
)
from mcp.server.auth.provider import (
    AuthorizationCode,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.server.auth.provider import (
    TokenVerifier as TokenVerifierProtocol,
)
from mcp.server.auth.routes import (
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import (
    ClientRegistrationOptions,
    RevocationOptions,
)
from pydantic import AnyHttpUrl, Field
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Route

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class AccessToken(_SDKAccessToken):
    """AccessToken that includes all JWT claims."""

    claims: dict[str, Any] = Field(default_factory=dict)


class TokenHandler(_SDKTokenHandler):
    """TokenHandler that returns OAuth 2.1 compliant error responses.

    The MCP SDK returns `unauthorized_client` for client authentication failures.
    However, per RFC 6749 Section 5.2, authentication failures should return
    `invalid_client` with HTTP 401, not `unauthorized_client`.

    This distinction matters: `unauthorized_client` means "client exists but
    can't do this", while `invalid_client` means "client doesn't exist or
    credentials are wrong". Claude's OAuth client uses this to decide whether
    to re-register.

    This handler transforms 401 responses with `unauthorized_client` to use
    `invalid_client` instead, making the error semantics correct per OAuth spec.
    """

    async def handle(self, request: Any):
        """Wrap SDK handle() and transform auth error responses."""
        response = await super().handle(request)

        # Transform 401 unauthorized_client -> invalid_client
        if response.status_code == 401:
            try:
                body = json.loads(response.body)
                if body.get("error") == "unauthorized_client":
                    return PydanticJSONResponse(
                        content=TokenErrorResponse(
                            error="invalid_client",
                            error_description=body.get("error_description"),
                        ),
                        status_code=401,
                        headers={
                            "Cache-Control": "no-store",
                            "Pragma": "no-cache",
                        },
                    )
            except (json.JSONDecodeError, AttributeError):
                pass  # Not JSON or unexpected format, return as-is

        return response


class AuthProvider(TokenVerifierProtocol):
    """Base class for all FastMCP authentication providers.

    This class provides a unified interface for all authentication providers,
    whether they are simple token verifiers or full OAuth authorization servers.
    All providers must be able to verify tokens and can optionally provide
    custom authentication routes.
    """

    def __init__(
        self,
        base_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
    ):
        """
        Initialize the auth provider.

        Args:
            base_url: The base URL of this server (e.g., http://localhost:8000).
                This is used for constructing .well-known endpoints and OAuth metadata.
            required_scopes: List of OAuth scopes required for all requests.
        """
        if isinstance(base_url, str):
            base_url = AnyHttpUrl(base_url)
        self.base_url = base_url
        self.required_scopes = required_scopes or []
        self._mcp_path: str | None = None
        self._resource_url: AnyHttpUrl | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token and return access info if valid.

        All auth providers must implement token verification.

        Args:
            token: The token string to validate

        Returns:
            AccessToken object if valid, None if invalid or expired
        """
        raise NotImplementedError("Subclasses must implement verify_token")

    def set_mcp_path(self, mcp_path: str | None) -> None:
        """Set the MCP endpoint path and compute resource URL.

        This method is called by get_routes() to configure the expected
        resource URL before route creation. Subclasses can override to
        perform additional initialization that depends on knowing the
        MCP endpoint path.

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
        """
        self._mcp_path = mcp_path
        self._resource_url = self._get_resource_url(mcp_path)

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get all routes for this authentication provider.

        This includes both well-known discovery routes and operational routes.
        Each provider is responsible for creating whatever routes it needs:
        - TokenVerifier: typically no routes (default implementation)
        - RemoteAuthProvider: protected resource metadata routes
        - OAuthProvider: full OAuth authorization server routes
        - Custom providers: whatever routes they need

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
                This is used to advertise the resource URL in metadata, but the
                provider does not create the actual MCP endpoint route.

        Returns:
            List of all routes for this provider (excluding the MCP endpoint itself)
        """
        return []

    def get_well_known_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get well-known discovery routes for this authentication provider.

        This is a utility method that filters get_routes() to return only
        well-known discovery routes (those starting with /.well-known/).

        Well-known routes provide OAuth metadata and discovery endpoints that
        clients use to discover authentication capabilities. These routes should
        be mounted at the root level of the application to comply with RFC 8414
        and RFC 9728.

        Common well-known routes:
        - /.well-known/oauth-authorization-server (authorization server metadata)
        - /.well-known/oauth-protected-resource/* (protected resource metadata)

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
                This is used to construct path-scoped well-known URLs.

        Returns:
            List of well-known discovery routes (typically mounted at root level)
        """
        all_routes = self.get_routes(mcp_path)
        return [
            route
            for route in all_routes
            if isinstance(route, Route) and route.path.startswith("/.well-known/")
        ]

    def get_middleware(self) -> list:
        """Get HTTP application-level middleware for this auth provider.

        Returns:
            List of Starlette Middleware instances to apply to the HTTP app
        """
        # TODO(ty): remove type ignores when ty supports Starlette Middleware typing
        return [
            Middleware(
                AuthenticationMiddleware,  # type: ignore[arg-type]
                backend=BearerAuthBackend(self),
            ),
            Middleware(AuthContextMiddleware),  # type: ignore[arg-type]
        ]

    def _get_resource_url(self, path: str | None = None) -> AnyHttpUrl | None:
        """Get the actual resource URL being protected.

        Args:
            path: The path where the resource endpoint is mounted (e.g., "/mcp")

        Returns:
            The full URL of the protected resource
        """
        if self.base_url is None:
            return None

        if path:
            prefix = str(self.base_url).rstrip("/")
            suffix = path.lstrip("/")
            return AnyHttpUrl(f"{prefix}/{suffix}")
        return self.base_url


class TokenVerifier(AuthProvider):
    """Base class for token verifiers (Resource Servers).

    This class provides token verification capability without OAuth server functionality.
    Token verifiers typically don't provide authentication routes by default.
    """

    def __init__(
        self,
        base_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
    ):
        """
        Initialize the token verifier.

        Args:
            base_url: The base URL of this server
            required_scopes: Scopes that are required for all requests
        """
        super().__init__(base_url=base_url, required_scopes=required_scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token and return access info if valid."""
        raise NotImplementedError("Subclasses must implement verify_token")


class RemoteAuthProvider(AuthProvider):
    """Authentication provider for resource servers that verify tokens from known authorization servers.

    This provider composes a TokenVerifier with authorization server metadata to create
    standardized OAuth 2.0 Protected Resource endpoints (RFC 9728). Perfect for:
    - JWT verification with known issuers
    - Remote token introspection services
    - Any resource server that knows where its tokens come from

    Use this when you have token verification logic and want to advertise
    the authorization servers that issue valid tokens.
    """

    base_url: AnyHttpUrl

    def __init__(
        self,
        token_verifier: TokenVerifier,
        authorization_servers: list[AnyHttpUrl],
        base_url: AnyHttpUrl | str,
        resource_name: str | None = None,
        resource_documentation: AnyHttpUrl | None = None,
    ):
        """Initialize the remote auth provider.

        Args:
            token_verifier: TokenVerifier instance for token validation
            authorization_servers: List of authorization servers that issue valid tokens
            base_url: The base URL of this server
            resource_name: Optional name for the protected resource
            resource_documentation: Optional documentation URL for the protected resource
        """
        super().__init__(
            base_url=base_url,
            required_scopes=token_verifier.required_scopes,
        )
        self.token_verifier = token_verifier
        self.authorization_servers = authorization_servers
        self.resource_name = resource_name
        self.resource_documentation = resource_documentation

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify token using the configured token verifier."""
        return await self.token_verifier.verify_token(token)

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get routes for this provider.

        Creates protected resource metadata routes (RFC 9728).
        """
        routes = []

        # Get the resource URL based on the MCP path
        resource_url = self._get_resource_url(mcp_path)

        if resource_url:
            # Add protected resource metadata routes
            routes.extend(
                create_protected_resource_routes(
                    resource_url=resource_url,
                    authorization_servers=self.authorization_servers,
                    scopes_supported=self.token_verifier.required_scopes,
                    resource_name=self.resource_name,
                    resource_documentation=self.resource_documentation,
                )
            )

        return routes


class OAuthProvider(
    AuthProvider,
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken],
):
    """OAuth Authorization Server provider.

    This class provides full OAuth server functionality including client registration,
    authorization flows, token issuance, and token verification.
    """

    def __init__(
        self,
        *,
        base_url: AnyHttpUrl | str,
        issuer_url: AnyHttpUrl | str | None = None,
        service_documentation_url: AnyHttpUrl | str | None = None,
        client_registration_options: ClientRegistrationOptions | None = None,
        revocation_options: RevocationOptions | None = None,
        required_scopes: list[str] | None = None,
    ):
        """
        Initialize the OAuth provider.

        Args:
            base_url: The public URL of this FastMCP server
            issuer_url: The issuer URL for OAuth metadata (defaults to base_url)
            service_documentation_url: The URL of the service documentation.
            client_registration_options: The client registration options.
            revocation_options: The revocation options.
            required_scopes: Scopes that are required for all requests.
        """

        super().__init__(base_url=base_url, required_scopes=required_scopes)

        if issuer_url is None:
            self.issuer_url = self.base_url
        elif isinstance(issuer_url, str):
            self.issuer_url = AnyHttpUrl(issuer_url)
        else:
            self.issuer_url = issuer_url

        # Log if issuer_url and base_url differ (requires additional setup)
        if (
            self.base_url is not None
            and self.issuer_url is not None
            and str(self.base_url) != str(self.issuer_url)
        ):
            logger.info(
                f"OAuth endpoints at {self.base_url}, issuer at {self.issuer_url}. "
                f"Ensure well-known routes are accessible at root ({self.issuer_url}/.well-known/). "
                f"See: https://gofastmcp.com/deployment/http#mounting-authenticated-servers"
            )

        # Initialize OAuth Authorization Server Provider
        OAuthAuthorizationServerProvider.__init__(self)

        if isinstance(service_documentation_url, str):
            service_documentation_url = AnyHttpUrl(service_documentation_url)

        self.service_documentation_url = service_documentation_url
        self.client_registration_options = client_registration_options
        self.revocation_options = revocation_options

    async def verify_token(self, token: str) -> AccessToken | None:
        """
        Verify a bearer token and return access info if valid.

        This method implements the TokenVerifier protocol by delegating
        to our existing load_access_token method.

        Args:
            token: The token string to validate

        Returns:
            AccessToken object if valid, None if invalid or expired
        """
        return await self.load_access_token(token)

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get OAuth authorization server routes and optional protected resource routes.

        This method creates the full set of OAuth routes including:
        - Standard OAuth authorization server routes (/.well-known/oauth-authorization-server, /authorize, /token, etc.)
        - Optional protected resource routes

        Returns:
            List of OAuth routes
        """
        # Configure resource URL before creating routes
        self.set_mcp_path(mcp_path)

        # Create standard OAuth authorization server routes
        # Pass base_url as issuer_url to ensure metadata declares endpoints where
        # they're actually accessible (operational routes are mounted at
        # base_url)
        assert self.base_url is not None  # typing check
        assert (
            self.issuer_url is not None
        )  # typing check (issuer_url defaults to base_url)

        sdk_routes = create_auth_routes(
            provider=self,
            issuer_url=self.base_url,
            service_documentation_url=self.service_documentation_url,
            client_registration_options=self.client_registration_options,
            revocation_options=self.revocation_options,
        )

        # Replace the token endpoint with our custom handler that returns
        # proper OAuth 2.1 error codes (invalid_client instead of unauthorized_client)
        oauth_routes: list[Route] = []
        for route in sdk_routes:
            if (
                isinstance(route, Route)
                and route.path == "/token"
                and route.methods is not None
                and "POST" in route.methods
            ):
                # Replace with our OAuth 2.1 compliant token handler
                token_handler = TokenHandler(
                    provider=self, client_authenticator=ClientAuthenticator(self)
                )
                oauth_routes.append(
                    Route(
                        path="/token",
                        endpoint=cors_middleware(
                            token_handler.handle, ["POST", "OPTIONS"]
                        ),
                        methods=["POST", "OPTIONS"],
                    )
                )
            else:
                oauth_routes.append(route)

        # Add protected resource routes if this server is also acting as a resource server
        if self._resource_url:
            supported_scopes = (
                self.client_registration_options.valid_scopes
                if self.client_registration_options
                and self.client_registration_options.valid_scopes
                else self.required_scopes
            )
            protected_routes = create_protected_resource_routes(
                resource_url=self._resource_url,
                authorization_servers=[cast(AnyHttpUrl, self.issuer_url)],
                scopes_supported=supported_scopes,
            )
            oauth_routes.extend(protected_routes)

        # Add base routes
        oauth_routes.extend(super().get_routes(mcp_path))

        return oauth_routes

    def get_well_known_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get well-known discovery routes with RFC 8414 path-aware support.

        Overrides the base implementation to support path-aware authorization
        server metadata discovery per RFC 8414. If issuer_url has a path component,
        the authorization server metadata route is adjusted to include that path.

        For example, if issuer_url is "http://example.com/api", the discovery
        endpoint will be at "/.well-known/oauth-authorization-server/api" instead
        of just "/.well-known/oauth-authorization-server".

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")

        Returns:
            List of well-known discovery routes
        """
        routes = super().get_well_known_routes(mcp_path)

        # RFC 8414: If issuer_url has a path, use path-aware discovery
        if self.issuer_url:
            parsed = urlparse(str(self.issuer_url))
            issuer_path = parsed.path.rstrip("/")

            if issuer_path and issuer_path != "/":
                # Replace /.well-known/oauth-authorization-server with path-aware version
                new_routes = []
                for route in routes:
                    if route.path == "/.well-known/oauth-authorization-server":
                        new_path = (
                            f"/.well-known/oauth-authorization-server{issuer_path}"
                        )
                        new_routes.append(
                            Route(
                                new_path,
                                endpoint=route.endpoint,
                                methods=route.methods,
                            )
                        )
                    else:
                        new_routes.append(route)
                return new_routes

        return routes
