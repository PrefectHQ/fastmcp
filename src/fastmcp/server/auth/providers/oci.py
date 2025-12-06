"""OCI OIDC provider for FastMCP.

The pull request for the provider is submitted to fastmcp.

This module provides OIDC Implementation to integrate MCP servers with OCI.
You only need OCI Identity Domain's discovery URL, client ID, client secret, and base URL.

Post Authentication, you get OCI IAM domain access token. That is not authorized to invoke OCI control plane.
You need to exchange the IAM domain access token for OCI UPST token to invoke OCI control plane APIs.
The sample code below has get_oci_signer function that returns OCI TokenExchangeSigner object.
You can use the signer object to create OCI service object.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.oci import OCIProvider
    from fastmcp.server.dependencies import get_access_token
    from fastmcp.utilities.logging import get_logger

    import os

    # Load configuration from environment
    FASTMCP_SERVER_AUTH_OCI_CONFIG_URL = os.environ["FASTMCP_SERVER_AUTH_OCI_CONFIG_URL"]
    FASTMCP_SERVER_AUTH_OCI_CLIENT_ID = os.environ["FASTMCP_SERVER_AUTH_OCI_CLIENT_ID"]
    FASTMCP_SERVER_AUTH_OCI_CLIENT_SECRET = os.environ["FASTMCP_SERVER_AUTH_OCI_CLIENT_SECRET"]
    FASTMCP_SERVER_AUTH_OCI_IAM_GUID = os.environ["FASTMCP_SERVER_AUTH_OCI_IAM_GUID"]

    import oci
    from oci.auth.signers import TokenExchangeSigner

    logger = get_logger(__name__)

    # Simple OCI OIDC protection
    auth = OCIProvider(
        config_url=FASTMCP_SERVER_AUTH_OCI_CONFIG_URL, #config URL is the OCI IAM Domain OIDC discovery URL.
        client_id=FASTMCP_SERVER_AUTH_OCI_CLIENT_ID, #This is same as the client ID configured for the OCI IAM Domain Integrated Application
        client_secret=FASTMCP_SERVER_AUTH_OCI_CLIENT_SECRET, #This is same as the client secret configured for the OCI IAM Domain Integrated Application
        required_scopes=["openid", "profile", "email"],
        redirect_path="/auth/callback",
        base_url="http://localhost:8000",
    )

    # NOTE: For production use, replace this with a thread-safe cache implementation
    # such as threading.Lock-protected dict or a proper caching library
    _global_token_cache = {} #In memory cache for OCI session token signer

    def get_oci_signer() -> TokenExchangeSigner:

        authntoken = get_access_token()
        tokenID = authntoken.claims.get("jti")
        token = authntoken.token

        #Check if the signer exists for the token ID in memory cache
        cached_signer = _global_token_cache.get(tokenID)
        logger.debug(f"Global cached signer: {cached_signer}")
        if cached_signer:
            logger.debug(f"Using globally cached signer for token ID: {tokenID}")
            return cached_signer

        #If the signer is not yet created for the token then create new OCI signer object
        logger.debug(f"Creating new signer for token ID: {tokenID}")
        signer = TokenExchangeSigner(
            jwt_or_func=token,
            oci_domain_id=FASTMCP_SERVER_AUTH_OCI_IAM_GUID.split(".")[0],   #This is same as IAM GUID configured for the OCI IAM Domain
            client_id=FASTMCP_SERVER_AUTH_OCI_CLIENT_ID, #This is same as the client ID configured for the OCI IAM Domain Integrated Application
            client_secret=FASTMCP_SERVER_AUTH_OCI_CLIENT_SECRET #This is same as the client secret configured for the OCI IAM Domain Integrated Application
        )
        logger.debug(f"Signer {signer} created for token ID: {tokenID}")

        #Cache the signer object in memory cache
        _global_token_cache[tokenID] = signer
        logger.debug(f"Signer cached for token ID: {tokenID}")

        return signer

    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class OCIProviderSettings(BaseSettings):
    """Settings for OCI IAM domain OIDC provider."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_OCI_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    config_url: AnyHttpUrl | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    audience: str | None = None
    base_url: AnyHttpUrl | None = None
    issuer_url: AnyHttpUrl | None = None
    redirect_path: str | None = None
    required_scopes: list[str] | None = None
    allowed_client_redirect_uris: list[str] | None = None
    jwt_signing_key: str | bytes | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class OCIProvider(OIDCProxy):
    """An OCI IAM Domain provider implementation for FastMCP.

    This provider is a complete OCI integration that's ready to use with
    just the configuration URL, client ID, client secret, and base URL.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.oci import OCIProvider

        # Simple OCI OIDC protection
        auth = OCIProvider(
            config_url=FASTMCP_SERVER_AUTH_OCI_CONFIG_URL, #config URL is the OCI IAM Domain OIDC discovery URL.
            client_id=FASTMCP_SERVER_AUTH_OCI_CLIENT_ID, #This is same as the client ID configured for the OCI IAM Domain Integrated Application
            client_secret=FASTMCP_SERVER_AUTH_OCI_CLIENT_SECRET, #This is same as the client secret configured for the OCI IAM Domain Integrated Application
            base_url="http://localhost:8000",
            required_scopes=["openid", "profile", "email"],
            redirect_path="/auth/callback",
        )

        mcp = FastMCP("My Protected Server", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        config_url: AnyHttpUrl | str | NotSetT = NotSet,
        client_id: str | NotSetT = NotSet,
        client_secret: str | NotSetT = NotSet,
        audience: str | NotSetT = NotSet,
        base_url: AnyHttpUrl | str | NotSetT = NotSet,
        issuer_url: AnyHttpUrl | str | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT = NotSet,
        redirect_path: str | NotSetT = NotSet,
        allowed_client_redirect_uris: list[str] | NotSetT = NotSet,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | NotSetT = NotSet,
        require_authorization_consent: bool = True,
    ) -> None:
        """Initialize OCI OIDC provider.

        Args:
            config_url: OCI OIDC Discovery URL
            client_id: OCI IAM Domain Integrated Application client id
            client_secret: OCI Integrated Application client secret
            audience: OCI API audience (optional)
            base_url: Public URL where OIDC endpoints will be accessible (includes any mount path)
            issuer_url: Issuer URL for OCI IAM Domain metadata. This will override issuer URL from the discovery URL.
            required_scopes: Required OCI scopes (defaults to ["openid"])
            redirect_path: Redirect path configured in OCI IAM Domain Integrated Application. The default is "/auth/callback".
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
        """

        overrides: dict[str, object] = {
            k: v
            for k, v in {
                "config_url": config_url,
                "client_id": client_id,
                "client_secret": client_secret,
                "audience": audience,
                "base_url": base_url,
                "issuer_url": issuer_url,
                "required_scopes": required_scopes,
                "redirect_path": redirect_path,
                "allowed_client_redirect_uris": allowed_client_redirect_uris,
                "jwt_signing_key": jwt_signing_key,
            }.items()
            if v is not NotSet
        }
        settings = OCIProviderSettings(**overrides)  # type: ignore[arg-type]

        if not settings.config_url:
            raise ValueError(
                "config_url is required - set via parameter or FASTMCP_SERVER_AUTH_OCI_CONFIG_URL"
            )

        if not settings.client_id:
            raise ValueError(
                "client_id is required - set via parameter or FASTMCP_SERVER_AUTH_OCI_CLIENT_ID"
            )

        if not settings.client_secret:
            raise ValueError(
                "client_secret is required - set via parameter or FASTMCP_SERVER_AUTH_OCI_CLIENT_SECRET"
            )

        if not settings.base_url:
            raise ValueError(
                "base_url is required - set via parameter or FASTMCP_SERVER_AUTH_OCI_BASE_URL"
            )

        oci_required_scopes = settings.required_scopes or ["openid"]

        super().__init__(
            config_url=settings.config_url,
            client_id=settings.client_id,
            client_secret=settings.client_secret.get_secret_value(),
            audience=settings.audience,
            base_url=settings.base_url,
            issuer_url=settings.issuer_url,
            redirect_path=settings.redirect_path,
            required_scopes=oci_required_scopes,
            allowed_client_redirect_uris=settings.allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=settings.jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
        )

        logger.debug(
            "Initialized OCI OAuth provider for client %s with scopes: %s",
            settings.client_id,
            oci_required_scopes,
        )
