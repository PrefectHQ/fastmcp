"""Azure AD B2C OAuth provider for FastMCP.

Azure AD B2C (Business-to-Consumer) is Microsoft's consumer identity service.
It differs from standard Microsoft Entra ID in three important ways:

- **Authority host**: `{tenant}.b2clogin.com` instead of
  `login.microsoftonline.com` (or a custom domain).
- **URL structure**: the policy name is embedded in the path:
  `/{tenant}.onmicrosoft.com/{policy}/oauth2/v2.0/...`
- **Scope identifier URI**: uses the `https://` scheme:
  `https://{tenant}.onmicrosoft.com/{client_id}/{scope}`

Additionally, B2C access token issuers carry the tenant GUID — not the
`.onmicrosoft.com` name — in the `iss` claim, and the exact value can
vary by policy or custom-domain configuration. This provider therefore
disables issuer validation by default; audience validation still enforces
that tokens are issued for the correct application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NoReturn

import httpx

from fastmcp.server.auth.providers.azure import OIDC_SCOPES, AzureProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from key_value.aio.protocols import AsyncKeyValue
    from pydantic import AnyHttpUrl

logger = get_logger(__name__)


class AzureB2CProvider(AzureProvider):
    """FastMCP OAuth provider pre-configured for Azure AD B2C user flows.

    Wraps `AzureProvider` with the three endpoint and scope adjustments that
    B2C requires; all other behaviour (PKCE, consent page, token storage) is
    inherited unchanged.

    **Note:** Azure AD B2C does **not** support the On-Behalf-Of (OBO) flow.
    Calling `get_obo_credential()` on this provider raises `NotImplementedError`.
    Use `AzureProvider` with standard Entra ID for OBO scenarios.

    Scope Handling:
    - `required_scopes`: provide unprefixed scope names (e.g. `["mcp-access"]`).
      They are automatically prefixed with the B2C identifier URI
      (`https://{tenant}.onmicrosoft.com/{client_id}/{scope}`) for
      authorization requests and token validation.
    - `additional_authorize_scopes`: provide full-URI scopes as with
      `AzureProvider`.

    Setup:
    1. Create a B2C tenant in the Azure Portal and define a user flow or custom
       policy (e.g. `B2C_1_susi`).
    2. Register an application in the B2C tenant.
    3. Under **Expose an API**, add the `https://{tenant}.onmicrosoft.com/{client_id}`
       application ID URI and define your custom scope names.
    4. Configure the Web platform redirect URI:
       `http://localhost:8000/auth/callback` (or your custom path).
    5. Create a client secret.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.azure_b2c import AzureB2CProvider

        auth = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="00000000-0000-0000-0000-000000000001",
            client_secret="my-secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
        )

        mcp = FastMCP("My App", auth=auth)
        ```

    Args:
        tenant_name: Short B2C tenant name without the `.onmicrosoft.com`
            suffix (e.g. `"mytenant"`).
        policy_name: User-flow or custom-policy name
            (e.g. `"B2C_1_susi"` or `"B2C_1A_SIGNUP_SIGNIN"`).
        client_id: Application (client) ID from the B2C app registration.
        client_secret: Client secret from the B2C app registration.  Optional
            when using alternative credentials via a custom
            `_create_upstream_oauth_client` override.
        required_scopes: Custom API scope names **without** prefix
            (e.g. `["mcp-access", "read"]`).  Automatically prefixed with
            the B2C identifier URI for authorization and token validation.
        base_url: Public base URL of this server (including any mount path).
        custom_domain: Optional custom domain for the B2C authority
            (e.g. `"auth.mycompany.com"`).  When omitted the default
            `{tenant_name}.b2clogin.com` is used.  Must be a bare hostname
            without scheme or trailing slash.
        token_issuer: Expected value of the `iss` claim in B2C access
            tokens.  When `None` (the default) issuer validation is
            **disabled** because B2C issuers embed the tenant GUID — which
            differs from the tenant name — and the exact format can vary by
            policy and custom-domain configuration.  Audience validation still
            enforces that tokens target the correct application.  Once you
            have confirmed the first successful round-trip you can read the
            actual `iss` value from the decoded claims and pass it here to
            enable strict validation.
        identifier_uri: Optional Application ID URI for your B2C API.  Defaults
            to `https://{tenant_name}.onmicrosoft.com/{client_id}`.  Override
            when your B2C app registration uses a different Application ID URI.
        resource_base_url: Optional public base URL for the protected resource
            metadata and token audience.  Defaults to `base_url`.
        issuer_url: Issuer URL for the FastMCP OAuth metadata document
            (defaults to `base_url`).  Use the root-level URL when the MCP
            server is mounted under a sub-path.
        redirect_path: Redirect path registered in the B2C app registration
            (defaults to `"/auth/callback"`).
        additional_authorize_scopes: Extra scopes to request during
            authorization in full-URI format.  Not validated on tokens and
            not advertised to MCP clients.
        allowed_client_redirect_uris: Allowed redirect URI patterns for MCP
            clients.  `None` (default) allows all URIs.
        client_storage: Storage backend for OAuth state.  Defaults to an
            encrypted file store derived from `platformdirs`.
        jwt_signing_key: Secret for signing FastMCP JWT tokens.
        require_authorization_consent: Whether to show a consent screen before
            redirecting to B2C (default `True`).  Set to `"external"` when
            consent is handled by the B2C user flow itself.
        consent_csp_policy: Optional `Content-Security-Policy` header value
            for the consent page.
        forward_resource: Forward the `resource` parameter to the upstream
            authorization endpoint (default `True`).
        fallback_refresh_token_expiry_seconds: Fallback lifetime for refresh
            tokens when the upstream does not advertise an expiry.
        http_client: Optional `httpx.AsyncClient` for JWKS fetches.
        enable_cimd: Enable CIMD (Client ID Metadata Document) support
            (default `True`).
    """

    def __init__(
        self,
        *,
        tenant_name: str,
        policy_name: str,
        client_id: str,
        client_secret: str | None = None,
        required_scopes: list[str],
        base_url: str,
        custom_domain: str | None = None,
        token_issuer: str | None = None,
        identifier_uri: str | None = None,
        resource_base_url: AnyHttpUrl | str | None = None,
        issuer_url: str | None = None,
        redirect_path: str | None = None,
        additional_authorize_scopes: list[str] | None = None,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool | Literal["remember", "external"] = True,
        consent_csp_policy: str | None = None,
        forward_resource: bool = True,
        fallback_refresh_token_expiry_seconds: int | None = None,
        http_client: httpx.AsyncClient | None = None,
        enable_cimd: bool = True,
    ) -> None:
        # --- Input validation ---
        if ".onmicrosoft.com" in tenant_name:
            raise ValueError(
                f"tenant_name should be the short name without the "
                f".onmicrosoft.com suffix (e.g. 'mytenant'), got {tenant_name!r}"
            )
        if "/" in tenant_name or "://" in tenant_name:
            raise ValueError(
                f"tenant_name must be a plain name without slashes or scheme, "
                f"got {tenant_name!r}"
            )
        if "/" in policy_name or "://" in policy_name:
            raise ValueError(
                f"policy_name must not contain slashes or scheme, got {policy_name!r}"
            )
        if custom_domain is not None:
            custom_domain = (
                custom_domain.removeprefix("https://")
                .removeprefix("http://")
                .rstrip("/")
            )
            if not custom_domain or "://" in custom_domain:
                raise ValueError(
                    "custom_domain must be a bare hostname "
                    "(e.g. 'auth.mycompany.com'), got an empty or invalid value"
                )

        b2c_authority = custom_domain or f"{tenant_name}.b2clogin.com"
        b2c_tenant_path = f"{tenant_name}.onmicrosoft.com/{policy_name}"
        b2c_identifier_uri = (
            identifier_uri or f"https://{tenant_name}.onmicrosoft.com/{client_id}"
        )

        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            tenant_id=b2c_tenant_path,
            required_scopes=required_scopes,
            base_url=base_url,
            resource_base_url=resource_base_url,
            identifier_uri=b2c_identifier_uri,
            issuer_url=issuer_url,
            redirect_path=redirect_path,
            additional_authorize_scopes=additional_authorize_scopes,
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
            consent_csp_policy=consent_csp_policy,
            forward_resource=forward_resource,
            fallback_refresh_token_expiry_seconds=fallback_refresh_token_expiry_seconds,
            base_authority=b2c_authority,
            http_client=http_client,
            enable_cimd=enable_cimd,
        )

        # AzureProvider sets issuer = f"https://{base_authority}/{tenant_id}/v2.0"
        # which embeds the policy name and doesn't match real B2C issuers.
        # B2C access tokens carry the tenant GUID in the `iss` claim; the format
        # varies by policy and custom-domain configuration.  Override with the
        # caller-supplied value, or None to disable strict issuer checking.
        if not isinstance(self._token_validator, JWTVerifier):
            raise TypeError(  # pragma: no cover
                f"Expected a JWTVerifier as token validator, "
                f"got {type(self._token_validator).__name__}"
            )
        self._token_validator.issuer = token_issuer

    async def get_obo_credential(self, user_assertion: str) -> NoReturn:
        """Reject OBO calls — Azure AD B2C does not support On-Behalf-Of.

        Raises:
            NotImplementedError: Always. B2C tenants cannot perform OBO
                token exchanges.
        """
        raise NotImplementedError(
            "Azure AD B2C does not support the On-Behalf-Of (OBO) flow. "
            "Use AzureProvider with standard Entra ID for OBO scenarios."
        )

    def _prefix_scopes_for_azure(self, scopes: list[str]) -> list[str]:
        """Prefix custom B2C scopes with the B2C `https://` identifier URI.

        B2C app scopes must be requested as
        `https://{tenant}.onmicrosoft.com/{client_id}/{scope}`.  Standard
        Entra ID uses `api://{client_id}/{scope}`; that prefix is wrong for
        B2C and will cause authorization failures.

        OIDC scopes and already-qualified URIs (containing `://` or `/`)
        are left unchanged.

        Args:
            scopes: Scope names, may be short or fully qualified.

        Returns:
            Scopes with the B2C identifier URI prefix applied where needed.
        """
        prefixed = []
        for scope in scopes:
            if scope in OIDC_SCOPES or "://" in scope or "/" in scope:
                prefixed.append(scope)
            else:
                prefixed.append(f"{self.identifier_uri}/{scope}")
        return prefixed
