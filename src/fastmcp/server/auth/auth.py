from __future__ import annotations

from typing import Any

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
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import (
    ClientRegistrationOptions,
    RevocationOptions,
)
from pydantic import AnyHttpUrl
from starlette.routing import Route


class AccessToken(_SDKAccessToken):
    """AccessToken that includes all JWT claims."""

    claims: dict[str, Any] = {}


class AuthProvider(TokenVerifierProtocol):
    """Base class for all FastMCP authentication providers.

    This class provides a unified interface for all authentication providers,
    whether they are simple token verifiers or full OAuth authorization servers.
    All providers must be able to verify tokens and can optionally provide
    custom authentication routes.
    """

    def __init__(self, resource_server_url: AnyHttpUrl | str | None = None):
        """
        Initialize the auth provider.

        Args:
            resource_server_url: The URL of this resource server. This is used
            for RFC 8707 resource indicators, including creating the WWW-Authenticate
            header.
        """
        if isinstance(resource_server_url, str):
            resource_server_url = AnyHttpUrl(resource_server_url)
        self.resource_server_url = resource_server_url

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token and return access info if valid.

        All auth providers must implement token verification.

        Args:
            token: The token string to validate

        Returns:
            AccessToken object if valid, None if invalid or expired
        """
        raise NotImplementedError("Subclasses must implement verify_token")

    def get_routes(self) -> list[Route]:
        """Get the routes for this authentication provider.

        Each provider is responsible for creating whatever routes it needs:
        - TokenVerifier: typically no routes (default implementation)
        - RemoteAuthProvider: protected resource metadata routes
        - OAuthProvider: full OAuth authorization server routes
        - Custom providers: whatever routes they need

        Returns:
            List of routes for this provider
        """
        return []

    def get_resource_metadata_url(self) -> AnyHttpUrl | None:
        """Get the resource metadata URL for RFC 9728 compliance."""
        if self.resource_server_url is None:
            return None

        # Add .well-known path for RFC 9728 compliance
        resource_metadata_url = AnyHttpUrl(
            str(self.resource_server_url).rstrip("/")
            + "/.well-known/oauth-protected-resource"
        )
        return resource_metadata_url


class TokenVerifier(AuthProvider):
    """Base class for token verifiers (Resource Servers).

    This class provides token verification capability without OAuth server functionality.
    Token verifiers typically don't provide authentication routes by default.
    """

    def __init__(
        self,
        resource_server_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
    ):
        """
        Initialize the token verifier.

        Args:
            resource_server_url: The URL of this resource server. This is used
            for RFC 8707 resource indicators, including creating the WWW-Authenticate
            header.
            required_scopes: Scopes that are required for all requests
        """
        super().__init__(resource_server_url=resource_server_url)
        self.required_scopes = required_scopes or []

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

    def __init__(
        self,
        token_verifier: TokenVerifier,
        authorization_servers: list[AnyHttpUrl],
        resource_server_url: AnyHttpUrl | str,
        resource_name: str | None = None,
        resource_documentation: AnyHttpUrl | None = None,
    ):
        """Initialize the remote auth provider.

        Args:
            token_verifier: TokenVerifier instance for token validation
            authorization_servers: List of authorization servers that issue valid tokens
            resource_server_url: URL of this resource server. This is used
            for RFC 8707 resource indicators, including creating the WWW-Authenticate
            header.
        """
        super().__init__(resource_server_url=resource_server_url)
        self.token_verifier = token_verifier
        self.authorization_servers = authorization_servers
        self.resource_name = resource_name
        self.resource_documentation = resource_documentation

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify token using the configured token verifier."""
        return await self.token_verifier.verify_token(token)

    def get_routes(self) -> list[Route]:
        """Get OAuth routes for this provider.

        By default, returns only the standardized OAuth 2.0 Protected Resource routes.
        Subclasses can override this method to add additional routes by calling
        super().get_routes() and extending the returned list.
        """
        assert self.resource_server_url is not None

        return create_protected_resource_routes(
            resource_url=self.resource_server_url,
            authorization_servers=self.authorization_servers,
            scopes_supported=self.token_verifier.required_scopes,
            resource_name=self.resource_name,
            resource_documentation=self.resource_documentation,
        )


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
        resource_server_url: AnyHttpUrl | str | None = None,
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
            resource_server_url: The URL of this resource server (for RFC 8707 resource indicators, defaults to base_url)
        """

        super().__init__()

        # Convert URLs to proper types
        if isinstance(base_url, str):
            base_url = AnyHttpUrl(base_url)
        self.base_url = base_url

        if issuer_url is None:
            self.issuer_url = base_url
        elif isinstance(issuer_url, str):
            self.issuer_url = AnyHttpUrl(issuer_url)
        else:
            self.issuer_url = issuer_url

        # Handle our own resource_server_url and required_scopes
        if resource_server_url is None:
            self.resource_server_url = base_url
        elif isinstance(resource_server_url, str):
            self.resource_server_url = AnyHttpUrl(resource_server_url)
        else:
            self.resource_server_url = resource_server_url
        self.required_scopes = required_scopes or []

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

    def get_routes(self) -> list[Route]:
        """Get OAuth authorization server routes and optional protected resource routes.

        This method creates the full set of OAuth routes including:
        - Standard OAuth authorization server routes (/.well-known/oauth-authorization-server, /authorize, /token, etc.)
        - Optional protected resource routes if resource_server_url is configured

        Returns:
            List of OAuth routes
        """

        # Create standard OAuth authorization server routes
        oauth_routes = create_auth_routes(
            provider=self,
            issuer_url=self.issuer_url,
            service_documentation_url=self.service_documentation_url,
            client_registration_options=self.client_registration_options,
            revocation_options=self.revocation_options,
        )

        # Add protected resource routes if this server is also acting as a resource server
        if self.resource_server_url:
            protected_routes = create_protected_resource_routes(
                resource_url=self.resource_server_url,
                authorization_servers=[self.issuer_url],
                scopes_supported=self.required_scopes,
            )
            oauth_routes.extend(protected_routes)

        return oauth_routes
