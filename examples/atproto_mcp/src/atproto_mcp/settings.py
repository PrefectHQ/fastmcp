from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=[".env"], extra="ignore")

    atproto_handle: str = Field(default=...)
    atproto_password: str = Field(default=...)
    atproto_pds_url: str = Field(default="https://bsky.social")

    atproto_notifications_default_limit: int = Field(default=10)
    atproto_timeline_default_limit: int = Field(default=10)
    atproto_search_default_limit: int = Field(default=10)


settings = Settings()
