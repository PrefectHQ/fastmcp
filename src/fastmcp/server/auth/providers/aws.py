"""AWS Cognito OAuth provider for FastMCP.

This module provides a complete AWS Cognito OAuth integration that's ready to use
with a user pool ID, domain prefix, client ID and client secret. It handles all
the complexity of AWS Cognito's OAuth flow, token validation, and user management.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.aws_cognito import AWSCognitoProvider

    # Simple AWS Cognito OAuth protection
    auth = AWSCognitoProvider(
        user_pool_id="your-user-pool-id",
        aws_region="eu-central-1",
        client_id="your-cognito-client-id",
        client_secret="your-cognito-client-secret"
    )

    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from __future__ import annotations

from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class AWSCognitoTokenVerifier(JWTVerifier):
    """Token verifier that filters claims to Cognito-specific subset."""

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify token and filter claims to Cognito-specific subset."""
        # Use base JWT verification
        access_token = await super().verify_token(token)
        if not access_token:
            return None

        # Filter claims to Cognito-specific subset
        cognito_claims = {
            "sub": access_token.claims.get("sub"),
            "username": access_token.claims.get("username"),
            "cognito:groups": access_token.claims.get("cognito:groups", []),
        }

        # Return new AccessToken with filtered claims
        return AccessToken(
            token=access_token.token,
            client_id=access_token.client_id,
            scopes=access_token.scopes,
            expires_at=access_token.expires_at,
            claims=cognito_claims,
        )


class AWSCognitoProvider(OIDCProxy):
    """Complete AWS Cognito OAuth provider for FastMCP.

    This provider makes it trivial to add AWS Cognito OAuth protection to any
    FastMCP server using OIDC Discovery. Just provide your Cognito User Pool details,
    client credentials, and a base URL, and you're ready to go.

    Features:
    - Automatic OIDC Discovery from AWS Cognito User Pool
    - Automatic JWT token validation via Cognito's public keys
    - Cognito-specific claim filtering (sub, username, cognito:groups)
    - Support for Cognito User Pools

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.aws_cognito import AWSCognitoProvider

        auth = AWSCognitoProvider(
            user_pool_id="eu-central-1_XXXXXXXXX",
            aws_region="eu-central-1",
            client_id="your-cognito-client-id",
            client_secret="your-cognito-client-secret",
            base_url="https://my-server.com",
            redirect_path="/custom/callback",
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        user_pool_id: str,
        client_id: str,
        client_secret: str,
        base_url: AnyHttpUrl | str,
        aws_region: str = "eu-central-1",
        issuer_url: AnyHttpUrl | str | None = None,
        redirect_path: str = "/auth/callback",
        required_scopes: list[str] | None = None,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool = True,
    ):
        """Initialize AWS Cognito OAuth provider.

        Args:
            user_pool_id: Your Cognito User Pool ID (e.g., "eu-central-1_XXXXXXXXX")
            client_id: Cognito app client ID
            client_secret: Cognito app client secret
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            aws_region: AWS region where your User Pool is located (defaults to "eu-central-1")
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path configured in Cognito app (defaults to "/auth/callback")
            required_scopes: Required Cognito scopes (defaults to ["openid"])
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, a DiskStore will be created in the data directory (derived from `platformdirs`). The
                disk store will be encrypted using a key derived from the JWT Signing Key.
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to AWS Cognito.
                When False, authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or testing environments.
        """
        # Parse scopes if provided as string
        required_scopes_final = (
            parse_scopes(required_scopes) if required_scopes is not None else ["openid"]
        )

        # Construct OIDC discovery URL
        config_url = f"https://cognito-idp.{aws_region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"

        # Store Cognito-specific info for claim filtering
        self.user_pool_id = user_pool_id
        self.aws_region = aws_region

        # Initialize OIDC proxy with Cognito discovery
        super().__init__(
            config_url=config_url,
            client_id=client_id,
            client_secret=client_secret,
            algorithm="RS256",
            required_scopes=required_scopes_final,
            base_url=base_url,
            issuer_url=issuer_url,
            redirect_path=redirect_path,
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
        )

        logger.debug(
            "Initialized AWS Cognito OAuth provider for client %s with scopes: %s",
            client_id,
            required_scopes_final,
        )

    def get_token_verifier(
        self,
        *,
        algorithm: str | None = None,
        audience: str | None = None,
        required_scopes: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> TokenVerifier:
        """Creates a Cognito-specific token verifier with claim filtering.

        Args:
            algorithm: Optional token verifier algorithm
            audience: Optional token verifier audience
            required_scopes: Optional token verifier required_scopes
            timeout_seconds: HTTP request timeout in seconds
        """
        return AWSCognitoTokenVerifier(
            issuer=str(self.oidc_config.issuer),
            audience=audience,
            algorithm=algorithm,
            jwks_uri=str(self.oidc_config.jwks_uri),
            required_scopes=required_scopes,
        )
