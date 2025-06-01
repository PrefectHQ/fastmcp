from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp.server.auth.providers.bearer import BearerAuthProvider


class NotSet(Enum):
    sentinel = 0


NOTSET = NotSet.sentinel


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
        public_key: str | None | NotSet = NOTSET,
        jwks_uri: str | None | NotSet = NOTSET,
        issuer: str | None | NotSet = NOTSET,
        audience: str | None | NotSet = NOTSET,
        required_scopes: list[str] | None | NotSet = NOTSET,
    ):
        kwargs = {
            "public_key": public_key,
            "jwks_uri": jwks_uri,
            "issuer": issuer,
            "audience": audience,
            "required_scopes": required_scopes,
        }
        settings = EnvBearerAuthProviderSettings(
            **{k: v for k, v in kwargs.items() if v is not NOTSET}
        )
        super().__init__(**settings.model_dump())
