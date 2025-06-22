from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    atproto_handle: str = Field(default=...)
    atproto_password: str = Field(default=...)
    atproto_pds_url: str = Field(default="https://bsky.social")


settings = Settings()
