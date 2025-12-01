"""Azure (Microsoft Entra) OAuth provider for FastMCP.

This provider implements Azure/Microsoft Entra ID OAuth authentication
using the OAuth Proxy pattern for non-DCR OAuth flows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

# Standard OIDC scopes that should never be prefixed with identifier_uri.
# Per Microsoft docs: https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc
# "OIDC scopes are requested as simple string identifiers without resource prefixes"
OIDC_SCOPES = frozenset({"openid", "profile", "email", "offline_access"})


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
    jwt_signing_key: str | None = None
    base_authority: str = "login.microsoftonline.com"

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

    Scope Handling:
    - required_scopes: Provide unprefixed scope names (e.g., ["read", "write"])
      → Automatically prefixed with identifier_uri during initialization
      → Validated on all tokens and advertised to MCP clients
    - additional_authorize_scopes: Provide full format (e.g., ["User.Read"])
      → NOT prefixed, NOT validated, NOT advertised to clients
      → Used to request Microsoft Graph or other upstream API permissions

    Features:
    - OAuth proxy to Azure/Microsoft identity platform
    - JWT validation using tenant issuer and JWKS
    - Supports tenant configurations: specific tenant ID, "organizations", or "consumers"
    - Custom API scopes and Microsoft Graph scopes in a single provider

    Setup:
    1. Create an App registration in Azure Portal
    2. Configure Web platform redirect URI: http://localhost:8000/auth/callback (or your custom path)
    3. Add an Application ID URI under "Expose an API" (defaults to api://{client_id})
    4. Add custom scopes (e.g., "read", "write") under "Expose an API"
    5. Set access token version to 2 in the App manifest: "requestedAccessTokenVersion": 2
    6. Create a client secret
    7. Get Application (client) ID, Directory (tenant) ID, and client secret

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.azure import AzureProvider

        # Standard Azure (Public Cloud)
        auth = AzureProvider(
            client_id="your-client-id",
            client_secret="your-client-secret",
            tenant_id="your-tenant-id",
            required_scopes=["read", "write"],  # Unprefixed scope names
            additional_authorize_scopes=["User.Read", "Mail.Read"],  # Optional Graph scopes
            base_url="http://localhost:8000",
            # identifier_uri defaults to api://{client_id}
        )

        # Azure Government
        auth_gov = AzureProvider(
            client_id="your-client-id",
            client_secret="your-client-secret",
            tenant_id="your-tenant-id",
            required_scopes=["read", "write"],
            base_authority="login.microsoftonline.us",  # Override for Azure Gov
            base_url="http://localhost:8000",
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
        identifier_uri: str | NotSetT | None = NotSet,
        base_url: str | NotSetT = NotSet,
        issuer_url: str | NotSetT = NotSet,
        redirect_path: str | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT | None = NotSet,
        additional_authorize_scopes: list[str] | NotSetT | None = NotSet,
        allowed_client_redirect_uris: list[str] | NotSetT = NotSet,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | NotSetT = NotSet,
        require_authorization_consent: bool = True,
        base_authority: str | NotSetT = NotSet,
    ) -> None:
        """Initialize Azure OAuth provider.

        Args:
            client_id: Azure application (client) ID from your App registration
            client_secret: Azure client secret from your App registration
            tenant_id: Azure tenant ID (specific tenant GUID, "organizations", or "consumers")
            identifier_uri: Optional Application ID URI for your custom API (defaults to api://{client_id}).
                This URI is automatically prefixed to all required_scopes during initialization.
                Example: identifier_uri="api://my-api" + required_scopes=["read"]
                → tokens validated for "api://my-api/read"
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path configured in Azure App registration (defaults to "/auth/callback")
            base_authority: Azure authority base URL (defaults to "login.microsoftonline.com").
                For Azure Government, use "login.microsoftonline.us".
            required_scopes: Custom API scope names WITHOUT prefix (e.g., ["read", "write"]).
                - Automatically prefixed with identifier_uri during initialization
                - Validated on all tokens
                - Advertised in Protected Resource Metadata
                - Must match scope names defined in Azure Portal under "Expose an API"
                Example: ["read", "write"] → validates tokens containing ["api://xxx/read", "api://xxx/write"]
            additional_authorize_scopes: Microsoft Graph or other upstream scopes in full format.
                - NOT prefixed with identifier_uri
                - NOT validated on tokens
                - NOT advertised to MCP clients
                - Used to request additional permissions from Azure (e.g., Graph API access)
                Example: ["User.Read", "Mail.Read", "offline_access"]
                These scopes allow your FastMCP server to call Microsoft Graph APIs using the
                upstream Azure token, but MCP clients are unaware of them.
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, a DiskStore will be created in the data directory (derived from `platformdirs`). The
                disk store will be encrypted using a key derived from the JWT Signing Key.
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
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
                    "jwt_signing_key": jwt_signing_key,
                    "base_authority": base_authority,
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

        # Validate required_scopes has at least one scope
        if not settings.required_scopes:
            msg = (
                "required_scopes must include at least one scope - set via parameter or "
                "FASTMCP_SERVER_AUTH_AZURE_REQUIRED_SCOPES. Azure's OAuth API requires "
                "the 'scope' parameter in authorization requests. Use the unprefixed scope "
                "names from your Azure App registration (e.g., ['read', 'write'])"
            )
            raise ValueError(msg)

        # Apply defaults
        self.identifier_uri = settings.identifier_uri or f"api://{settings.client_id}"
        self.additional_authorize_scopes = settings.additional_authorize_scopes or []
        tenant_id_final = settings.tenant_id

        # Always validate tokens against the app's API client ID using JWT
        base_authority_final = settings.base_authority
        issuer = f"https://{base_authority_final}/{tenant_id_final}/v2.0"
        jwks_uri = (
            f"https://{base_authority_final}/{tenant_id_final}/discovery/v2.0/keys"
        )

        # Azure access tokens only include custom API scopes in the `scp` claim,
        # NOT standard OIDC scopes (openid, profile, email, offline_access).
        # Filter out OIDC scopes from validation - they'll still be sent to Azure
        # during authorization (handled by _prefix_scopes_for_azure).
        validation_scopes = None
        if settings.required_scopes:
            validation_scopes = [
                s for s in settings.required_scopes if s not in OIDC_SCOPES
            ]
            # If all scopes were OIDC scopes, use None (no scope validation)
            if not validation_scopes:
                validation_scopes = None

        token_verifier = JWTVerifier(
            jwks_uri=jwks_uri,
            issuer=issuer,
            audience=settings.client_id,
            algorithm="RS256",
            required_scopes=validation_scopes,  # Only validate non-OIDC scopes
        )

        # Extract secret string from SecretStr
        client_secret_str = (
            settings.client_secret.get_secret_value() if settings.client_secret else ""
        )

        # Build Azure OAuth endpoints with tenant
        authorization_endpoint = (
            f"https://{base_authority_final}/{tenant_id_final}/oauth2/v2.0/authorize"
        )
        token_endpoint = (
            f"https://{base_authority_final}/{tenant_id_final}/oauth2/v2.0/token"
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
            jwt_signing_key=settings.jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
            # Advertise full scopes including OIDC (even though we only validate non-OIDC)
            valid_scopes=settings.required_scopes,
        )

        authority_info = ""
        if base_authority_final != "login.microsoftonline.com":
            authority_info = f" using authority {base_authority_final}"
        logger.info(
            "Initialized Azure OAuth provider for client %s with tenant %s%s%s",
            settings.client_id,
            tenant_id_final,
            f" and identifier_uri {self.identifier_uri}" if self.identifier_uri else "",
            authority_info,
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
        # Don't modify the scopes in params - they stay unprefixed for MCP clients
        # We'll prefix them when building the Azure authorization URL (in _build_upstream_authorize_url)
        auth_url = await super().authorize(client, params_to_use)
        separator = "&" if "?" in auth_url else "?"
        return f"{auth_url}{separator}prompt=select_account"

    def _prefix_scopes_for_azure(self, scopes: list[str]) -> list[str]:
        """Prefix unprefixed custom API scopes with identifier_uri for Azure.

        This helper centralizes the scope prefixing logic used in both
        authorization and token refresh flows.

        Scopes that are NOT prefixed:
        - Standard OIDC scopes (openid, profile, email, offline_access)
        - Fully-qualified URIs (contain "://")
        - Scopes with path component (contain "/")

        Note: Microsoft Graph scopes (e.g., User.Read) should be passed via
        `additional_authorize_scopes` or use fully-qualified format
        (e.g., https://graph.microsoft.com/User.Read).

        Args:
            scopes: List of scopes, may be prefixed or unprefixed

        Returns:
            List of scopes with identifier_uri prefix applied where needed
        """
        prefixed = []
        for scope in scopes:
            if scope in OIDC_SCOPES:
                # Standard OIDC scopes - never prefix
                prefixed.append(scope)
            elif "://" in scope or "/" in scope:
                # Already fully-qualified (e.g., "api://xxx/read" or
                # "https://graph.microsoft.com/User.Read")
                prefixed.append(scope)
            else:
                # Unprefixed custom API scope - prefix with identifier_uri
                prefixed.append(f"{self.identifier_uri}/{scope}")
        return prefixed

    def _build_upstream_authorize_url(
        self, txn_id: str, transaction: dict[str, Any]
    ) -> str:
        """Build Azure authorization URL with prefixed scopes.

        Overrides parent to prefix scopes with identifier_uri before sending to Azure,
        while keeping unprefixed scopes in the transaction for MCP clients.
        """
        # Get unprefixed scopes from transaction
        unprefixed_scopes = transaction.get("scopes") or self.required_scopes or []

        # Prefix scopes for Azure authorization request
        prefixed_scopes = self._prefix_scopes_for_azure(unprefixed_scopes)

        # Add Microsoft Graph scopes (not validated, not prefixed)
        if self.additional_authorize_scopes:
            prefixed_scopes.extend(self.additional_authorize_scopes)

        # Temporarily modify transaction dict for parent's URL building
        modified_transaction = transaction.copy()
        modified_transaction["scopes"] = prefixed_scopes

        # Let parent build the URL with prefixed scopes
        return super()._build_upstream_authorize_url(txn_id, modified_transaction)

    def _prepare_scopes_for_upstream_refresh(self, scopes: list[str]) -> list[str]:
        """Prepare scopes for Azure token refresh.

        Azure requires:
        1. Fully-qualified custom scopes (e.g., "api://xxx/read" not "read")
        2. Microsoft Graph scopes (e.g., "User.Read", "openid") sent as-is
        3. Additional scopes from provider config (additional_authorize_scopes)

        This method transforms base client scopes for Azure while keeping them
        unprefixed in storage to prevent accumulation.

        Args:
            scopes: Base scopes from RefreshToken (unprefixed, e.g., ["read"])

        Returns:
            Deduplicated list of scopes formatted for Azure token endpoint
        """
        logger.debug("Base scopes from storage: %s", scopes)

        # Filter out any additional_authorize_scopes that may have been stored
        # (they shouldn't be in storage, but clean them up if they are)
        additional_scopes_set = set(self.additional_authorize_scopes or [])
        base_scopes = [s for s in scopes if s not in additional_scopes_set]

        # Prefix base scopes with identifier_uri for Azure using shared helper
        prefixed_scopes = self._prefix_scopes_for_azure(base_scopes)

        # Add additional scopes (Graph + OIDC) for the Azure request
        # These are NOT stored in RefreshToken, only sent to Azure
        if self.additional_authorize_scopes:
            prefixed_scopes.extend(self.additional_authorize_scopes)

        # Deduplicate while preserving order (in case older tokens have duplicates)
        # Use dict.fromkeys() for O(n) deduplication with order preservation
        deduplicated_scopes = list(dict.fromkeys(prefixed_scopes))

        logger.debug("Scopes for Azure token endpoint: %s", deduplicated_scopes)
        return deduplicated_scopes
