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

    import oci
    from oci.auth.signers import TokenExchangeSigner

    logger = get_logger(__name__)

    # Load configuration from environment
    config_url = os.environ.get("OCI_CONFIG_URL")  # OCI IAM Domain OIDC discovery URL
    client_id = os.environ.get("OCI_CLIENT_ID")  # Client ID configured for the OCI IAM Domain Integrated Application
    client_secret = os.environ.get("OCI_CLIENT_SECRET")  # Client secret configured for the OCI IAM Domain Integrated Application
    iam_guid = os.environ.get("OCI_IAM_GUID")  # IAM GUID configured for the OCI IAM Domain

    # Simple OCI OIDC protection
    auth = OCIProvider(
        config_url=config_url,  # config URL is the OCI IAM Domain OIDC discovery URL
        client_id=client_id,  # This is same as the client ID configured for the OCI IAM Domain Integrated Application
        client_secret=client_secret,  # This is same as the client secret configured for the OCI IAM Domain Integrated Application
        required_scopes=["openid", "profile", "email"],
        redirect_path="/auth/callback",
        base_url="http://localhost:8000",
    )

    # NOTE: For production use, replace this with a thread-safe cache implementation
    # such as threading.Lock-protected dict or a proper caching library
    _global_token_cache = {}  # In memory cache for OCI session token signer

    def get_oci_signer() -> TokenExchangeSigner:

        authntoken = get_access_token()
        tokenID = authntoken.claims.get("jti")
        token = authntoken.token

        # Check if the signer exists for the token ID in memory cache
        cached_signer = _global_token_cache.get(tokenID)
        logger.debug(f"Global cached signer: {cached_signer}")
        if cached_signer:
            logger.debug(f"Using globally cached signer for token ID: {tokenID}")
            return cached_signer

        # If the signer is not yet created for the token then create new OCI signer object
        logger.debug(f"Creating new signer for token ID: {tokenID}")
        signer = TokenExchangeSigner(
            jwt_or_func=token,
            oci_domain_id=iam_guid.split(".")[0] if iam_guid else None,  # This is same as IAM GUID configured for the OCI IAM Domain
            client_id=client_id,  # This is same as the client ID configured for the OCI IAM Domain Integrated Application
            client_secret=client_secret,  # This is same as the client secret configured for the OCI IAM Domain Integrated Application
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
from pydantic import AnyHttpUrl

from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class OCIProvider(OIDCProxy):
    """An OCI IAM Domain provider implementation for FastMCP.

    This provider is a complete OCI integration that's ready to use with
    just the configuration URL, client ID, client secret, and base URL.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.oci import OCIProvider

        import os

        # Load configuration from environment
        auth = OCIProvider(
            config_url=os.environ.get("OCI_CONFIG_URL"),  # OCI IAM Domain OIDC discovery URL
            client_id=os.environ.get("OCI_CLIENT_ID"),  # Client ID configured for the OCI IAM Domain Integrated Application
            client_secret=os.environ.get("OCI_CLIENT_SECRET"),  # Client secret configured for the OCI IAM Domain Integrated Application
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
        config_url: AnyHttpUrl | str,
        client_id: str,
        client_secret: str,
        base_url: AnyHttpUrl | str,
        audience: str | None = None,
        issuer_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
        redirect_path: str | None = None,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool = True,
    ) -> None:
        """Initialize OCI OIDC provider.

        Args:
            config_url: OCI OIDC Discovery URL
            client_id: OCI IAM Domain Integrated Application client id
            client_secret: OCI Integrated Application client secret
            base_url: Public URL where OIDC endpoints will be accessible (includes any mount path)
            audience: OCI API audience (optional)
            issuer_url: Issuer URL for OCI IAM Domain metadata. This will override issuer URL from the discovery URL.
            required_scopes: Required OCI scopes (defaults to ["openid"])
            redirect_path: Redirect path configured in OCI IAM Domain Integrated Application. The default is "/auth/callback".
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
        """
        # Parse scopes if provided as string
        oci_required_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else ["openid"]
        )

        super().__init__(
            config_url=config_url,
            client_id=client_id,
            client_secret=client_secret,
            audience=audience,
            base_url=base_url,
            issuer_url=issuer_url,
            redirect_path=redirect_path,
            required_scopes=oci_required_scopes,
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
        )

        logger.debug(
            "Initialized OCI OAuth provider for client %s with scopes: %s",
            client_id,
            oci_required_scopes,
        )
