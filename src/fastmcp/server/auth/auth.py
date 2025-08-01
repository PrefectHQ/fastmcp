from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.server.auth.provider import (
    TokenVerifier as TokenVerifierProtocol,
)
from mcp.server.auth.settings import (
    ClientRegistrationOptions,
    RevocationOptions,
)
from pydantic import AnyHttpUrl
from starlette.routing import Route

if TYPE_CHECKING:
    pass


class AuthProvider:
    """Base class for all FastMCP authentication providers.

    This class provides a unified interface for all authentication providers,
    whether they are simple token verifiers or full OAuth authorization servers.
    All providers must be able to verify tokens and can optionally provide
    custom authentication routes.
    """

    def __init__(self, required_scopes: list[str] | None = None):
        """Initialize the auth provider."""
        self.required_scopes: list[str] = required_scopes or []

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token and return access info if valid.

        All auth providers must implement token verification.

        Args:
            token: The token string to validate

        Returns:
            AccessToken object if valid, None if invalid or expired
        """
        raise NotImplementedError("Subclasses must implement verify_token")

    def customize_auth_routes(self, routes: list[Route]) -> list[Route]:
        """Customize authentication routes after standard creation.

        This method allows providers to modify or add to the standard OAuth routes.
        The default implementation returns the routes unchanged.

        Args:
            routes: List of standard routes (may be empty for token-only providers)

        Returns:
            List of routes (potentially modified or extended)
        """
        return routes


class TokenVerifier(AuthProvider, TokenVerifierProtocol):
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
            resource_server_url: The URL of this resource server (for RFC 8707 resource indicators)
            required_scopes: Scopes that are required for all requests
        """
        # Initialize AuthProvider (no args needed)
        AuthProvider.__init__(self, required_scopes=required_scopes)

        # Handle our own resource_server_url and required_scopes
        self.resource_server_url: AnyHttpUrl | None
        if resource_server_url is None:
            self.resource_server_url = None
        elif isinstance(resource_server_url, str):
            self.resource_server_url = AnyHttpUrl(resource_server_url)
        else:
            self.resource_server_url = resource_server_url

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token and return access info if valid."""
        raise NotImplementedError("Subclasses must implement verify_token")


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

    def customize_auth_routes(self, routes: list[Route]) -> list[Route]:
        """Customize OAuth authentication routes after standard creation.

        This method allows providers to modify the standard OAuth routes
        returned by create_auth_routes. The default implementation returns
        the routes unchanged.

        Args:
            routes: List of standard OAuth routes from create_auth_routes

        Returns:
            List of routes (potentially modified)
        """
        return routes
