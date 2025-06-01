from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp.server.auth.providers.bearer import BearerAuthProvider


class NotSet:
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
    A BearerAuthProvider that loads settings from environment variables.
    """

    def __init__(
        self,
        public_key: str | None | type[NotSet] = NotSet,
        jwks_uri: str | None | type[NotSet] = NotSet,
        issuer: str | None | type[NotSet] = NotSet,
        audience: str | None | type[NotSet] = NotSet,
        required_scopes: list[str] | None | type[NotSet] = NotSet,
    ):
        kwargs = {
            "public_key": public_key,
            "jwks_uri": jwks_uri,
            "issuer": issuer,
            "audience": audience,
            "required_scopes": required_scopes,
        }
        settings = EnvBearerAuthProviderSettings(
            **{k: v for k, v in kwargs.items() if v is not NotSet}
        )
        super().__init__(**settings.model_dump())
