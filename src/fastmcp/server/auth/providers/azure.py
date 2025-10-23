"""Azure (Microsoft Entra) OAuth provider for FastMCP.

This provider implements Azure/Microsoft Entra ID OAuth authentication
using the OAuth Proxy pattern for non-DCR OAuth flows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from key_value.aio.protocols import AsyncKeyValue
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

if TYPE_CHECKING:
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

logger = get_logger(__name__)


class AzureProviderSettings(BaseSettings):
    """Settings for Azure OAuth provider."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_AZURE_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    client_id: str | None = None
    client_secret: SecretStr | None = None
    tenant_id: str | None = None
    identifier_uri: str | None = None
    base_url: str | None = None
    issuer_url: str | None = None
    redirect_path: str | None = None
    required_scopes: list[str] | None = None
    additional_authorize_scopes: list[str] | None = None
    allowed_client_redirect_uris: list[str] | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v: object) -> list[str] | None:
        return parse_scopes(v)

    @field_validator("additional_authorize_scopes", mode="before")
    @classmethod
    def _parse_additional_authorize_scopes(cls, v: object) -> list[str] | None:
        return parse_scopes(v)


class AzureProvider(OAuthProxy):
    """Azure (Microsoft Entra) OAuth provider for FastMCP.

    This provider implements Azure/Microsoft Entra ID authentication using the
    OAuth Proxy pattern. It supports both organizational accounts and personal
    Microsoft accounts depending on the tenant configuration.

    Features:
    - OAuth proxy to Azure/Microsoft identity platform
    - JWT validation using tenant issuer and JWKS
    - Supports tenant configurations: specific tenant ID, "organizations", or "consumers"

    Setup:
    1. Create an App registration in Azure Portal
    2. Configure Web platform redirect URI: http://localhost:8000/auth/callback (or your custom path)
    3. Add an Application ID URI. Either use the default (api://{client_id}) or set a custom one.
    4. Add a custom scope.
    5. Create a client secret.
    6. Get Application (client) ID, Directory (tenant) ID, and client secret

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.azure import AzureProvider

        auth = AzureProvider(
            client_id="your-client-id",
            client_secret="your-client-secret",
            tenant_id="your-tenant-id",
            required_scopes=["your-scope"],
            base_url="http://localhost:8000",
            # identifier_uri defaults to api://{client_id}
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        client_id: str | NotSetT = NotSet,
        client_secret: str | NotSetT = NotSet,
        tenant_id: str | NotSetT = NotSet,
        identifier_uri: str | None | NotSetT = NotSet,
        base_url: str | NotSetT = NotSet,
        issuer_url: str | NotSetT = NotSet,
        redirect_path: str | NotSetT = NotSet,
        required_scopes: list[str] | None | NotSetT = NotSet,
        additional_authorize_scopes: list[str] | None | NotSetT = NotSet,
        allowed_client_redirect_uris: list[str] | NotSetT = NotSet,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        token_encryption_key: str | bytes | None = None,
        require_authorization_consent: bool = True,
    ) -> None:
        """Initialize Azure OAuth provider.

        Args:
            client_id: Azure application (client) ID
            client_secret: Azure client secret
            tenant_id: Azure tenant ID (your specific tenant ID, "organizations", or "consumers")
            identifier_uri: Optional Application ID URI for your API. (defaults to api://{client_id})
                Used only to prefix scopes in authorization requests. Tokens are always validated
                against your app's client ID.
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path configured in Azure (defaults to "/auth/callback")
            required_scopes: Required scopes. These are validated on tokens and used as defaults
                when the client does not request specific scopes.
            additional_authorize_scopes: Additional scopes to include in the authorization request
                without prefixing. Use this to request upstream scopes such as Microsoft Graph
                permissions. These are not used for token validation.
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: An AsyncKeyValue-compatible store for client registrations, registrations are stored in memory if not provided
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes).
                None (default): Auto-managed via system keyring (Mac/Windows) or ephemeral (Linux).
                Explicit value: For production deployments. Recommended to store in environment variable.
            token_encryption_key: Secret for encrypting upstream tokens at rest (any string or bytes).
                None (default): Auto-managed via system keyring (Mac/Windows) or ephemeral (Linux).
                Explicit value: For production deployments. Recommended to store in environment variable.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to Azure.
                When False, authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or testing environments.
        """
        settings = AzureProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "tenant_id": tenant_id,
                    "identifier_uri": identifier_uri,
                    "base_url": base_url,
                    "issuer_url": issuer_url,
                    "redirect_path": redirect_path,
                    "required_scopes": required_scopes,
                    "additional_authorize_scopes": additional_authorize_scopes,
                    "allowed_client_redirect_uris": allowed_client_redirect_uris,
                }.items()
                if v is not NotSet
            }
        )

        # Validate required settings
        if not settings.client_id:
            msg = "client_id is required - set via parameter or FASTMCP_SERVER_AUTH_AZURE_CLIENT_ID"
            raise ValueError(msg)
        if not settings.client_secret:
            msg = "client_secret is required - set via parameter or FASTMCP_SERVER_AUTH_AZURE_CLIENT_SECRET"
            raise ValueError(msg)

        # Validate tenant_id is provided
        if not settings.tenant_id:
            msg = (
                "tenant_id is required - set via parameter or "
                "FASTMCP_SERVER_AUTH_AZURE_TENANT_ID. Use your Azure tenant ID "
                "(found in Azure Portal), 'organizations', or 'consumers'"
            )
            raise ValueError(msg)

        if not settings.required_scopes:
            raise ValueError("required_scopes is required")

        # Apply defaults
        self.identifier_uri = settings.identifier_uri or f"api://{settings.client_id}"
        self.additional_authorize_scopes = settings.additional_authorize_scopes or []
        tenant_id_final = settings.tenant_id

        # Always validate tokens against the app's API client ID using JWT
        issuer = f"https://login.microsoftonline.com/{tenant_id_final}/v2.0"
        jwks_uri = (
            f"https://login.microsoftonline.com/{tenant_id_final}/discovery/v2.0/keys"
        )

        token_verifier = JWTVerifier(
            jwks_uri=jwks_uri,
            issuer=issuer,
            audience=settings.client_id,
            algorithm="RS256",
            required_scopes=settings.required_scopes,
        )

        # Extract secret string from SecretStr
        client_secret_str = (
            settings.client_secret.get_secret_value() if settings.client_secret else ""
        )

        # Build Azure OAuth endpoints with tenant
        authorization_endpoint = (
            f"https://login.microsoftonline.com/{tenant_id_final}/oauth2/v2.0/authorize"
        )
        token_endpoint = (
            f"https://login.microsoftonline.com/{tenant_id_final}/oauth2/v2.0/token"
        )

        # Initialize OAuth proxy with Azure endpoints
        super().__init__(
            upstream_authorization_endpoint=authorization_endpoint,
            upstream_token_endpoint=token_endpoint,
            upstream_client_id=settings.client_id,
            upstream_client_secret=client_secret_str,
            token_verifier=token_verifier,
            base_url=settings.base_url,
            redirect_path=settings.redirect_path,
            issuer_url=settings.issuer_url
            or settings.base_url,  # Default to base_url if not specified
            allowed_client_redirect_uris=settings.allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            token_encryption_key=token_encryption_key,
            require_authorization_consent=require_authorization_consent,
        )

        logger.info(
            "Initialized Azure OAuth provider for client %s with tenant %s%s",
            settings.client_id,
            tenant_id_final,
            f" and identifier_uri {self.identifier_uri}" if self.identifier_uri else "",
        )

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Start OAuth transaction and redirect to Azure AD.

        Override parent's authorize method to filter out the 'resource' parameter
        which is not supported by Azure AD v2.0 endpoints. The v2.0 endpoints use
        scopes to determine the resource/audience instead of a separate parameter.

        Args:
            client: OAuth client information
            params: Authorization parameters from the client

        Returns:
            Authorization URL to redirect the user to Azure AD
        """
        # Clear the resource parameter that Azure AD v2.0 doesn't support
        # This parameter comes from RFC 8707 (OAuth 2.0 Resource Indicators)
        # but Azure AD v2.0 uses scopes instead to determine the audience
        params_to_use = params
        if hasattr(params, "resource"):
            original_resource = getattr(params, "resource", None)
            if original_resource is not None:
                params_to_use = params.model_copy(update={"resource": None})
                if original_resource:
                    logger.debug(
                        "Filtering out 'resource' parameter '%s' for Azure AD v2.0 (use scopes instead)",
                        original_resource,
                    )
        original_scopes = params_to_use.scopes or self.required_scopes
        prefixed_scopes = (
            self._add_prefix_to_scopes(original_scopes)
            if self.identifier_uri
            else original_scopes
        )

        final_scopes = list(prefixed_scopes)
        if self.additional_authorize_scopes:
            final_scopes.extend(self.additional_authorize_scopes)

        modified_params = params_to_use.model_copy(update={"scopes": final_scopes})

        auth_url = await super().authorize(client, modified_params)
        separator = "&" if "?" in auth_url else "?"
        return f"{auth_url}{separator}prompt=select_account"

    def _add_prefix_to_scopes(self, scopes: list[str]) -> list[str]:
        """Add Application ID URI prefix for authorization request."""
        return [f"{self.identifier_uri}/{scope}" for scope in scopes]
