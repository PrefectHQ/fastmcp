"""OIDC Proxy Provider for FastMCP.

This provider acts as a transparent proxy to an upstream OIDC compliant Authorization
Server. It leverages the OAuthProxy class to handle Dynamic Client Registration and
forwarding of all OAuth flows.

This implementation is based on:
    OpenID Connect Discovery 1.0 - https://openid.net/specs/openid-connect-discovery-1_0.html
    OAuth 2.0 Authorization Server Metadata - https://datatracker.ietf.org/doc/html/rfc8414
"""

from collections.abc import Sequence

import httpx
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl, BaseModel, model_validator
from typing_extensions import Self

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class OIDCConfiguration(BaseModel):
    """OIDC Configuration.

    See:
        https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderMetadata
        https://datatracker.ietf.org/doc/html/rfc8414#section-2
    """

    strict: bool = True

    # OpenID Connect Discovery 1.0
    issuer: AnyHttpUrl | str | None = None  # Strict

    authorization_endpoint: AnyHttpUrl | str | None = None  # Strict
    token_endpoint: AnyHttpUrl | str | None = None  # Strict
    userinfo_endpoint: AnyHttpUrl | str | None = None

    jwks_uri: AnyHttpUrl | str | None = None  # Strict

    registration_endpoint: AnyHttpUrl | str | None = None

    scopes_supported: Sequence[str] | None = None

    response_types_supported: Sequence[str] | None = None  # Strict
    response_modes_supported: Sequence[str] | None = None

    grant_types_supported: Sequence[str] | None = None

    acr_values_supported: Sequence[str] | None = None

    subject_types_supported: Sequence[str] | None = None  # Strict

    id_token_signing_alg_values_supported: Sequence[str] | None = None  # Strict
    id_token_encryption_alg_values_supported: Sequence[str] | None = None
    id_token_encryption_enc_values_supported: Sequence[str] | None = None

    userinfo_signing_alg_values_supported: Sequence[str] | None = None
    userinfo_encryption_alg_values_supported: Sequence[str] | None = None
    userinfo_encryption_enc_values_supported: Sequence[str] | None = None

    request_object_signing_alg_values_supported: Sequence[str] | None = None
    request_object_encryption_alg_values_supported: Sequence[str] | None = None
    request_object_encryption_enc_values_supported: Sequence[str] | None = None

    token_endpoint_auth_methods_supported: Sequence[str] | None = None
    token_endpoint_auth_signing_alg_values_supported: Sequence[str] | None = None

    display_values_supported: Sequence[str] | None = None

    claim_types_supported: Sequence[str] | None = None
    claims_supported: Sequence[str] | None = None

    service_documentation: AnyHttpUrl | str | None = None

    claims_locales_supported: Sequence[str] | None = None
    ui_locales_supported: Sequence[str] | None = None

    claims_parameter_supported: bool | None = None
    request_parameter_supported: bool | None = None
    request_uri_parameter_supported: bool | None = None

    require_request_uri_registration: bool | None = None

    op_policy_uri: AnyHttpUrl | str | None = None
    op_tos_uri: AnyHttpUrl | str | None = None

    # OAuth 2.0 Authorization Server Metadata
    revocation_endpoint: AnyHttpUrl | str | None = None
    revocation_endpoint_auth_methods_supported: Sequence[str] | None = None
    revocation_endpoint_auth_signing_alg_values_supported: Sequence[str] | None = None

    introspection_endpoint: AnyHttpUrl | str | None = None
    introspection_endpoint_auth_methods_supported: Sequence[str] | None = None
    introspection_endpoint_auth_signing_alg_values_supported: Sequence[str] | None = (
        None
    )

    code_challenge_methods_supported: Sequence[str] | None = None

    signed_metadata: str | None = None

    @model_validator(mode="after")
    def _enforce_strict(self) -> Self:
        """Enforce strict rules."""
        if not self.strict:
            return self

        def enforce(attr: str, is_url: bool = False) -> None:
            value = getattr(self, attr, None)
            if not value:
                message = f"Missing required configuration metadata: {attr}"
                logger.error(message)
                raise ValueError(message)

            if not is_url or isinstance(value, AnyHttpUrl):
                return

            try:
                AnyHttpUrl(value)
            except Exception as e:
                message = f"Invalid URL for configuration metadata: {attr}"
                logger.error(message)
                raise ValueError(message) from e

        enforce("issuer", True)
        enforce("authorization_endpoint", True)
        enforce("token_endpoint", True)
        enforce("jwks_uri", True)
        enforce("response_types_supported")
        enforce("subject_types_supported")
        enforce("id_token_signing_alg_values_supported")

        return self

    @classmethod
    def get_oidc_configuration(
        cls, config_url: AnyHttpUrl, *, strict: bool | None, timeout_seconds: int | None
    ) -> Self:
        """Get the OIDC configuration for the specified config URL.

        Args:
            config_url: The OIDC config URL
            strict: The strict flag for the configuration
            timeout_seconds: HTTP request timeout in seconds
        """
        get_kwargs = {}
        if timeout_seconds is not None:
            get_kwargs["timeout"] = timeout_seconds

        try:
            response = httpx.get(str(config_url), **get_kwargs)
            response.raise_for_status()

            config_data = response.json()
            if strict is not None:
                config_data["strict"] = strict

            return cls.model_validate(config_data)
        except Exception:
            logger.exception(
                f"Unable to get OIDC configuration for config url: {config_url}"
            )
            raise


class OIDCProxy(OAuthProxy):
    """OAuth provider that wraps OAuthProxy to provide configuration via an OIDC configuration URL.

    This provider makes it easier to add OAuth protection for any upstream provider
    that is OIDC compliant.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.oidc_proxy import OIDCProxy

        # Simple OIDC based protection
        auth = OIDCProxy(
            config_url="https://oidc.config.url",
            client_id="your-oidc-client-id",
            client_secret="your-oidc-client-secret",
            base_url="https://your.server.url",
        )

        mcp = FastMCP("My Protected Server", auth=auth)
        ```
    """

    oidc_config: OIDCConfiguration

    def __init__(
        self,
        *,
        # OIDC configuration
        config_url: AnyHttpUrl | str,
        strict: bool | None = None,
        # Upstream server configuration
        client_id: str,
        client_secret: str,
        audience: str | None = None,
        timeout_seconds: int | None = None,
        # Token verifier
        algorithm: str | None = None,
        required_scopes: list[str] | None = None,
        # FastMCP server configuration
        base_url: AnyHttpUrl | str,
        issuer_url: AnyHttpUrl | str | None = None,
        redirect_path: str | None = None,
        # Client configuration
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        # JWT and encryption keys
        jwt_signing_key: str | bytes | None = None,
        # Token validation configuration
        token_endpoint_auth_method: str | None = None,
        # Consent screen configuration
        require_authorization_consent: bool = True,
    ) -> None:
        """Initialize the OIDC proxy provider.

        Args:
            config_url: URL of upstream configuration
            strict: Optional strict flag for the configuration
            client_id: Client ID registered with upstream server
            client_secret: Client secret for upstream server
            audience: Audience for upstream server
            timeout_seconds: HTTP request timeout in seconds
            algorithm: Token verifier algorithm
            required_scopes: Required OAuth scopes
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path configured in upstream OAuth app (defaults to "/auth/callback")
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                Patterns support wildcards (e.g., "http://localhost:*", "https://*.example.com/*").
                If None (default), only localhost redirect URIs are allowed.
                If empty list, all redirect URIs are allowed (not recommended for production).
                These are for MCP clients performing loopback redirects, NOT for the upstream OAuth app.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, a DiskStore will be created in the data directory (derived from `platformdirs`). The
                disk store will be encrypted using a key derived from the JWT Signing Key.
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            token_endpoint_auth_method: Token endpoint authentication method for upstream server.
                Common values: "client_secret_basic", "client_secret_post", "none".
                If None, authlib will use its default (typically "client_secret_basic").
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to the upstream IdP.
                When False, authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or testing environments.
        """
        if not config_url:
            raise ValueError("Missing required config URL")

        if not client_id:
            raise ValueError("Missing required client id")

        if not client_secret:
            raise ValueError("Missing required client secret")

        if not base_url:
            raise ValueError("Missing required base URL")

        if isinstance(config_url, str):
            config_url = AnyHttpUrl(config_url)

        self.oidc_config = self.get_oidc_configuration(
            config_url, strict, timeout_seconds
        )
        if (
            not self.oidc_config.authorization_endpoint
            or not self.oidc_config.token_endpoint
        ):
            logger.debug(f"Invalid OIDC Configuration: {self.oidc_config}")
            raise ValueError("Missing required OIDC endpoints")

        revocation_endpoint = (
            str(self.oidc_config.revocation_endpoint)
            if self.oidc_config.revocation_endpoint
            else None
        )

        token_verifier = self.get_token_verifier(
            algorithm=algorithm,
            audience=audience,
            required_scopes=required_scopes,
            timeout_seconds=timeout_seconds,
        )

        init_kwargs = {
            "upstream_authorization_endpoint": str(
                self.oidc_config.authorization_endpoint
            ),
            "upstream_token_endpoint": str(self.oidc_config.token_endpoint),
            "upstream_client_id": client_id,
            "upstream_client_secret": client_secret,
            "upstream_revocation_endpoint": revocation_endpoint,
            "token_verifier": token_verifier,
            "base_url": base_url,
            "issuer_url": issuer_url or base_url,
            "service_documentation_url": self.oidc_config.service_documentation,
            "allowed_client_redirect_uris": allowed_client_redirect_uris,
            "client_storage": client_storage,
            "jwt_signing_key": jwt_signing_key,
            "token_endpoint_auth_method": token_endpoint_auth_method,
            "require_authorization_consent": require_authorization_consent,
        }

        if redirect_path:
            init_kwargs["redirect_path"] = redirect_path

        if audience:
            extra_params = {"audience": audience}
            init_kwargs["extra_authorize_params"] = extra_params
            init_kwargs["extra_token_params"] = extra_params

        super().__init__(**init_kwargs)

    def get_oidc_configuration(
        self,
        config_url: AnyHttpUrl,
        strict: bool | None,
        timeout_seconds: int | None,
    ) -> OIDCConfiguration:
        """Gets the OIDC configuration for the specified configuration URL.

        Args:
            config_url: The OIDC configuration URL
            strict: The strict flag for the configuration
            timeout_seconds: HTTP request timeout in seconds
        """
        return OIDCConfiguration.get_oidc_configuration(
            config_url, strict=strict, timeout_seconds=timeout_seconds
        )

    def get_token_verifier(
        self,
        *,
        algorithm: str | None = None,
        audience: str | None = None,
        required_scopes: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> TokenVerifier:
        """Creates the token verifier for the specified OIDC configuration and arguments.

        Args:
            algorithm: Optional token verifier algorithm
            audience: Optional token verifier audience
            required_scopes: Optional token verifier required_scopes
            timeout_seconds: HTTP request timeout in seconds
        """
        return JWTVerifier(
            jwks_uri=str(self.oidc_config.jwks_uri),
            issuer=str(self.oidc_config.issuer),
            algorithm=algorithm,
            audience=audience,
            required_scopes=required_scopes,
        )
