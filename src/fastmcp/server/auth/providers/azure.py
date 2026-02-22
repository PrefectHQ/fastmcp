"""Azure (Microsoft Entra) OAuth provider for FastMCP.

This provider implements Azure/Microsoft Entra ID OAuth authentication
using the OAuth Proxy pattern for non-DCR OAuth flows.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, cast

from key_value.aio.protocols import AsyncKeyValue

from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import decode_jwt_payload, parse_scopes
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from azure.identity.aio import OnBehalfOfCredential
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

logger = get_logger(__name__)

# Standard OIDC scopes that should never be prefixed with identifier_uri.
# Per Microsoft docs: https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc
# "OIDC scopes are requested as simple string identifiers without resource prefixes"
OIDC_SCOPES = frozenset({"openid", "profile", "email", "offline_access"})


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
        client_id: str,
        client_secret: str,
        tenant_id: str,
        required_scopes: list[str],
        base_url: str,
        identifier_uri: str | None = None,
        issuer_url: str | None = None,
        redirect_path: str | None = None,
        additional_authorize_scopes: list[str] | None = None,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool = True,
        base_authority: str = "login.microsoftonline.com",
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
                Example: ["User.Read", "Mail.Read"]
                These scopes allow your FastMCP server to call Microsoft Graph APIs using the
                upstream Azure token, but MCP clients are unaware of them.
                Note: "offline_access" is automatically included to obtain refresh tokens.
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, an encrypted file store will be created in the data directory
                (derived from `platformdirs`).
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to Azure.
                When False, authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or testing environments.
        """
        # Parse scopes if provided as string
        parsed_required_scopes = parse_scopes(required_scopes)
        parsed_additional_scopes: list[str] = (
            parse_scopes(additional_authorize_scopes) or []
            if additional_authorize_scopes
            else []
        )

        # Always include offline_access to get refresh tokens from Azure
        if "offline_access" not in parsed_additional_scopes:
            parsed_additional_scopes = [*parsed_additional_scopes, "offline_access"]

        # Store Azure-specific config for OBO credential creation
        self._tenant_id = tenant_id
        self._base_authority = base_authority

        # Cache of OBO credentials keyed by hash of user assertion token.
        # Reusing credentials allows the Azure SDK's internal token cache
        # to avoid redundant OBO exchanges for the same user + scopes.
        self._obo_credentials: OrderedDict[str, OnBehalfOfCredential] = OrderedDict()
        self._obo_max_credentials: int = 128

        # Apply defaults
        self.identifier_uri = identifier_uri or f"api://{client_id}"
        self.additional_authorize_scopes: list[str] = parsed_additional_scopes

        # Always validate tokens against the app's API client ID using JWT
        issuer = f"https://{base_authority}/{tenant_id}/v2.0"
        jwks_uri = f"https://{base_authority}/{tenant_id}/discovery/v2.0/keys"

        # Azure access tokens only include custom API scopes in the `scp` claim,
        # NOT standard OIDC scopes (openid, profile, email, offline_access).
        # Filter out OIDC scopes from validation - they'll still be sent to Azure
        # during authorization (handled by _prefix_scopes_for_azure).
        if parsed_required_scopes:
            validation_scopes = [
                s for s in parsed_required_scopes if s not in OIDC_SCOPES
            ]
            # If all scopes were OIDC scopes, use None (no scope validation)
            if not validation_scopes:
                validation_scopes = None
        else:
            validation_scopes = None

        token_verifier = JWTVerifier(
            jwks_uri=jwks_uri,
            issuer=issuer,
            audience=client_id,
            algorithm="RS256",
            required_scopes=validation_scopes,  # Only validate non-OIDC scopes
        )

        # Build Azure OAuth endpoints with tenant
        authorization_endpoint = (
            f"https://{base_authority}/{tenant_id}/oauth2/v2.0/authorize"
        )
        token_endpoint = f"https://{base_authority}/{tenant_id}/oauth2/v2.0/token"

        # Initialize OAuth proxy with Azure endpoints
        # Remember there's hooks called, such as _prepare_scopes_for_token_exchange
        # and _prepare_scopes_for_upstream_refresh
        super().__init__(
            upstream_authorization_endpoint=authorization_endpoint,
            upstream_token_endpoint=token_endpoint,
            upstream_client_id=client_id,
            upstream_client_secret=client_secret,
            token_verifier=token_verifier,
            base_url=base_url,
            redirect_path=redirect_path,
            issuer_url=issuer_url or base_url,  # Default to base_url if not specified
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
            valid_scopes=parsed_required_scopes,
        )

        authority_info = ""
        if base_authority != "login.microsoftonline.com":
            authority_info = f" using authority {base_authority}"
        logger.info(
            "Initialized Azure OAuth provider for client %s with tenant %s%s%s",
            client_id,
            tenant_id,
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

    def _prepare_scopes_for_token_exchange(self, scopes: list[str]) -> list[str]:
        """Prepare scopes for Azure authorization code exchange.

        Azure requires scopes during token exchange (AADSTS28003 error if missing).
        Azure only allows ONE resource per token request (AADSTS28000), so we only
        include scopes for this API plus OIDC scopes.

        Args:
            scopes: Scopes from the authorization request (unprefixed)

        Returns:
            List of scopes for Azure token endpoint
        """
        # Prefix scopes for this API
        prefixed_scopes = self._prefix_scopes_for_azure(scopes or [])

        # Add OIDC scopes only (not other API scopes) to avoid AADSTS28000
        if self.additional_authorize_scopes:
            prefixed_scopes.extend(
                s for s in self.additional_authorize_scopes if s in OIDC_SCOPES
            )

        deduplicated = list(dict.fromkeys(prefixed_scopes))
        logger.debug("Token exchange scopes: %s", deduplicated)
        return deduplicated

    def _prepare_scopes_for_upstream_refresh(self, scopes: list[str]) -> list[str]:
        """Prepare scopes for Azure token refresh.

        Azure requires fully-qualified scopes and only allows ONE resource per
        token request (AADSTS28000). We include scopes for this API plus OIDC scopes.

        Args:
            scopes: Base scopes from RefreshToken (unprefixed, e.g., ["read"])

        Returns:
            Deduplicated list of scopes formatted for Azure token endpoint
        """
        logger.debug("Base scopes from storage: %s", scopes)

        # Filter out any additional_authorize_scopes that may have been stored
        additional_scopes_set = set(self.additional_authorize_scopes or [])
        base_scopes = [s for s in scopes if s not in additional_scopes_set]

        # Prefix base scopes with identifier_uri for Azure
        prefixed_scopes = self._prefix_scopes_for_azure(base_scopes)

        # Add OIDC scopes only (not other API scopes) to avoid AADSTS28000
        if self.additional_authorize_scopes:
            prefixed_scopes.extend(
                s for s in self.additional_authorize_scopes if s in OIDC_SCOPES
            )

        deduplicated_scopes = list(dict.fromkeys(prefixed_scopes))
        logger.debug("Scopes for Azure token endpoint: %s", deduplicated_scopes)
        return deduplicated_scopes

    async def _extract_upstream_claims(
        self, idp_tokens: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract claims from Azure token response to embed in FastMCP JWT.

        Decodes the Azure access token (which is a JWT) to extract user identity
        claims. This allows gateways to inspect upstream identity information by
        decoding the FastMCP JWT without needing server-side storage lookups.

        Azure access tokens contain claims like:
        - sub: Subject identifier (unique per user per application)
        - oid: Object ID (unique user identifier across Azure AD)
        - tid: Tenant ID
        - azp: Authorized party (client ID that requested the token)
        - name: Display name
        - given_name: First name
        - family_name: Last name
        - preferred_username: User principal name (email format)
        - upn: User Principal Name
        - email: Email address (if available)
        - roles: Application roles assigned to the user
        - groups: Group memberships (if configured)

        Args:
            idp_tokens: Full token response from Azure, containing access_token
                and potentially id_token.

        Returns:
            Dict of extracted claims, or None if extraction fails.
        """
        access_token = idp_tokens.get("access_token")
        if not access_token:
            return None

        try:
            # Azure access tokens are JWTs - decode without verification
            # (already validated by token_verifier during token exchange)
            payload = decode_jwt_payload(access_token)

            # Extract useful identity claims
            claims: dict[str, Any] = {}
            claim_keys = [
                "sub",
                "oid",
                "tid",
                "azp",
                "name",
                "given_name",
                "family_name",
                "preferred_username",
                "upn",
                "email",
                "roles",
                "groups",
            ]
            for claim in claim_keys:
                if claim in payload:
                    claims[claim] = payload[claim]

            if claims:
                logger.debug(
                    "Extracted %d Azure claims for embedding in FastMCP JWT",
                    len(claims),
                )
                return claims

            return None

        except Exception as e:
            logger.debug("Failed to extract Azure claims: %s", e)
            return None

    async def get_obo_credential(self, user_assertion: str) -> OnBehalfOfCredential:
        """Get a cached or new OnBehalfOfCredential for OBO token exchange.

        Credentials are cached by user assertion so the Azure SDK's internal
        token cache can avoid redundant OBO exchanges when the same user
        calls multiple tools with the same scopes.

        Args:
            user_assertion: The user's access token to exchange via OBO.

        Returns:
            A configured OnBehalfOfCredential ready for get_token() calls.

        Raises:
            ImportError: If azure-identity is not installed (requires fastmcp[azure]).
        """
        _require_azure_identity("OBO token exchange")
        from azure.identity.aio import OnBehalfOfCredential

        key = hashlib.sha256(user_assertion.encode()).hexdigest()

        if key in self._obo_credentials:
            self._obo_credentials.move_to_end(key)
            return self._obo_credentials[key]

        credential = OnBehalfOfCredential(
            tenant_id=self._tenant_id,
            client_id=self._upstream_client_id,
            client_secret=self._upstream_client_secret.get_secret_value(),
            user_assertion=user_assertion,
            authority=f"https://{self._base_authority}",
        )
        self._obo_credentials[key] = credential

        # Evict oldest if over capacity
        while len(self._obo_credentials) > self._obo_max_credentials:
            _, evicted = self._obo_credentials.popitem(last=False)
            await evicted.close()

        return credential

    async def close_obo_credentials(self) -> None:
        """Close all cached OBO credentials."""
        credentials = list(self._obo_credentials.values())
        self._obo_credentials.clear()
        for credential in credentials:
            try:
                await credential.close()
            except Exception:
                logger.debug("Error closing OBO credential", exc_info=True)


class AzureJWTVerifier(JWTVerifier):
    """JWT verifier pre-configured for Azure AD / Microsoft Entra ID.

    Auto-configures JWKS URI, issuer, audience, and scope handling from your
    Azure app registration details. Designed for Managed Identity and other
    token-verification-only scenarios where AzureProvider's full OAuth proxy
    isn't needed.

    Handles Azure's scope format automatically:
    - Validates tokens using short-form scopes (what Azure puts in ``scp`` claims)
    - Advertises full-URI scopes in OAuth metadata (what clients need to request)

    Example::

        from fastmcp.server.auth import RemoteAuthProvider
        from fastmcp.server.auth.providers.azure import AzureJWTVerifier
        from pydantic import AnyHttpUrl

        verifier = AzureJWTVerifier(
            client_id="your-client-id",
            tenant_id="your-tenant-id",
            required_scopes=["access_as_user"],
        )

        auth = RemoteAuthProvider(
            token_verifier=verifier,
            authorization_servers=[
                AnyHttpUrl("https://login.microsoftonline.com/your-tenant-id/v2.0")
            ],
            base_url="https://my-server.com",
        )
    """

    def __init__(
        self,
        *,
        client_id: str,
        tenant_id: str,
        required_scopes: list[str] | None = None,
        identifier_uri: str | None = None,
        base_authority: str = "login.microsoftonline.com",
    ):
        """Initialize Azure JWT verifier.

        Args:
            client_id: Azure application (client) ID from your App registration
            tenant_id: Azure tenant ID (specific tenant GUID, "organizations", or "consumers").
                For multi-tenant apps ("organizations" or "consumers"), issuer validation
                is skipped since Azure tokens carry the actual tenant GUID as issuer.
            required_scopes: Scope names as they appear in Azure Portal under "Expose an API"
                (e.g., ["access_as_user", "read"]). These are validated against
                the short-form scopes in token ``scp`` claims, and automatically
                prefixed with identifier_uri for OAuth metadata.
            identifier_uri: Application ID URI (defaults to ``api://{client_id}``).
                Used to prefix scopes in OAuth metadata so clients know the full
                scope URIs to request from Azure.
            base_authority: Azure authority base URL (defaults to "login.microsoftonline.com").
                For Azure Government, use "login.microsoftonline.us".
        """
        self._identifier_uri = identifier_uri or f"api://{client_id}"

        # For multi-tenant apps, Azure tokens carry the actual tenant GUID as
        # issuer, not the literal "organizations" or "consumers" string. Skip
        # issuer validation for these — audience still protects against wrong-app tokens.
        multi_tenant_values = {"organizations", "consumers", "common"}
        issuer: str | None = (
            None
            if tenant_id in multi_tenant_values
            else f"https://{base_authority}/{tenant_id}/v2.0"
        )

        super().__init__(
            jwks_uri=f"https://{base_authority}/{tenant_id}/discovery/v2.0/keys",
            issuer=issuer,
            audience=client_id,
            algorithm="RS256",
            required_scopes=required_scopes,
        )

    @property
    def scopes_supported(self) -> list[str]:
        """Return scopes with Azure URI prefix for OAuth metadata.

        Azure tokens contain short-form scopes (e.g., ``read``) in the ``scp``
        claim, but clients must request full URI scopes (e.g.,
        ``api://client-id/read``) from the Azure authorization endpoint. This
        property returns the full-URI form for OAuth metadata while
        ``required_scopes`` retains the short form for token validation.
        """
        if not self.required_scopes:
            return []
        prefixed = []
        for scope in self.required_scopes:
            if scope in OIDC_SCOPES or "://" in scope or "/" in scope:
                prefixed.append(scope)
            else:
                prefixed.append(f"{self._identifier_uri}/{scope}")
        return prefixed


# --- Dependency injection support ---
# These require fastmcp[azure] extra for azure-identity

# Check if DI engine is available
try:
    from docket.dependencies import Dependency
except ImportError:
    from fastmcp._vendor.docket_di import Dependency


def _require_azure_identity(feature: str) -> None:
    """Raise ImportError with install instructions if azure-identity is not available."""
    try:
        import azure.identity  # noqa: F401
    except ImportError as e:
        raise ImportError(
            f"{feature} requires the `azure` extra. "
            "Install with: pip install 'fastmcp[azure]'"
        ) from e


class _EntraOBOToken(Dependency):  # type: ignore[misc]
    """Dependency that performs OBO token exchange for Microsoft Entra.

    Uses azure.identity's OnBehalfOfCredential for async-native OBO,
    with automatic token caching and refresh. Credentials are cached on
    the AzureProvider so repeated tool calls reuse existing credentials
    and benefit from the Azure SDK's internal token cache.
    """

    def __init__(self, scopes: list[str]):
        self.scopes = scopes

    async def __aenter__(self) -> str:
        _require_azure_identity("EntraOBOToken")

        from fastmcp.server.dependencies import get_access_token, get_server

        access_token = get_access_token()
        if access_token is None:
            raise RuntimeError(
                "No access token available. Cannot perform OBO exchange."
            )

        server = get_server()
        if not isinstance(server.auth, AzureProvider):
            raise RuntimeError(
                "EntraOBOToken requires an AzureProvider as the auth provider. "
                f"Current provider: {type(server.auth).__name__}"
            )

        credential = await server.auth.get_obo_credential(
            user_assertion=access_token.token,
        )

        result = await credential.get_token(*self.scopes)
        return result.token


def EntraOBOToken(scopes: list[str]) -> str:
    """Exchange the user's Entra token for a downstream API token via OBO.

    This dependency performs a Microsoft Entra On-Behalf-Of (OBO) token exchange,
    allowing your MCP server to call downstream APIs (like Microsoft Graph) on
    behalf of the authenticated user.

    Args:
        scopes: The scopes to request for the downstream API. For Microsoft Graph,
            use scopes like ["https://graph.microsoft.com/Mail.Read"] or
            ["https://graph.microsoft.com/.default"].

    Returns:
        A dependency that resolves to the downstream API access token string

    Raises:
        ImportError: If fastmcp[azure] is not installed
        RuntimeError: If no access token is available, provider is not Azure,
            or OBO exchange fails

    Example:
        ```python
        from fastmcp.server.auth.providers.azure import EntraOBOToken
        import httpx

        @mcp.tool()
        async def get_my_emails(
            graph_token: str = EntraOBOToken(["https://graph.microsoft.com/Mail.Read"])
        ):
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me/messages",
                    headers={"Authorization": f"Bearer {graph_token}"}
                )
                return resp.json()
        ```

    Note:
        For OBO to work, ensure the scopes are included in the AzureProvider's
        `additional_authorize_scopes` parameter, and that admin consent has been
        granted for those scopes in your Entra app registration.
    """
    return cast(str, _EntraOBOToken(scopes))
