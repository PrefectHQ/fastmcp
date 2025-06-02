from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp.server.auth.providers.bearer import BearerAuthProvider


# Sentinel object to indicate that a setting is not set
class _NotSet:
    pass


class EnvBearerAuthProviderSettings(BaseSettings):
    """Settings for the BearerAuthProvider."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_AUTH_BEARER_",
        env_file=".env",
        extra="ignore",
    )

    public_key: str | None = None
    jwks_uri: str | None = None
    issuer: str | None = None
    audience: str | None = None
    required_scopes: list[str] | None = None


class EnvBearerAuthProvider(BearerAuthProvider):
    """
    A BearerAuthProvider that loads settings from environment variables. Any
    providing setting will always take precedence over the environment
    variables.
    """

    def __init__(
        self,
        public_key: str | None | type[_NotSet] = _NotSet,
        jwks_uri: str | None | type[_NotSet] = _NotSet,
        issuer: str | None | type[_NotSet] = _NotSet,
        audience: str | None | type[_NotSet] = _NotSet,
        required_scopes: list[str] | None | type[_NotSet] = _NotSet,
    ):
        """
        Initialize the provider.

        Args:
            public_key: RSA public key in PEM format (for static key)
            jwks_uri: URI to fetch keys from (for key rotation)
            issuer: Expected issuer claim (optional)
            audience: Expected audience claim (optional)
            required_scopes: List of required scopes for access (optional)
        """
        kwargs = {
            "public_key": public_key,
            "jwks_uri": jwks_uri,
            "issuer": issuer,
            "audience": audience,
            "required_scopes": required_scopes,
        }
        settings = EnvBearerAuthProviderSettings(
            **{k: v for k, v in kwargs.items() if v is not _NotSet}
        )
        super().__init__(**settings.model_dump())
