"""Keycloak authentication provider for FastMCP."""

from __future__ import annotations

from pydantic import AnyHttpUrl

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class KeycloakAuthProvider(RemoteAuthProvider):
    """Keycloak authentication provider using Dynamic Client Registration (DCR).

    Requires Keycloak 26.6.0 or later, which includes the fix for DCR compatibility
    with MCP clients (https://github.com/keycloak/keycloak/pull/45309).

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

        auth = KeycloakAuthProvider(
            realm_url="https://keycloak.example.com/realms/myrealm",
            base_url="https://my-mcp-server.example.com",
        )

        mcp = FastMCP("My App", auth=auth)
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
        """Initialize the Keycloak auth provider.

        Args:
            realm_url: Keycloak realm URL (e.g., "https://keycloak.example.com/realms/myrealm")
            base_url: Public URL of this FastMCP server
            required_scopes: Scopes to require on incoming tokens. Defaults to
                ["openid"], which ensures the `sub` claim (user identifier) is
                present in the access token. Override to require additional scopes.
            audience: Optional audience(s) for JWT validation. Recommended for production.
            token_verifier: Optional custom token verifier. Defaults to a JWTVerifier
                configured for Keycloak's JWKS endpoint and issuer.
        """
        self.realm_url = str(realm_url).rstrip("/")
        parsed_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else ["openid"]
        )

        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.realm_url}/protocol/openid-connect/certs",
                issuer=self.realm_url,
                algorithm="RS256",
                required_scopes=parsed_scopes,
                audience=audience,
            )

        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(self.realm_url)],
            base_url=AnyHttpUrl(str(base_url).rstrip("/")),
        )
