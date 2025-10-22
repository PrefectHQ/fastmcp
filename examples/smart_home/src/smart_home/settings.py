from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    hue_bridge_ip: str = Field(default=...)
    hue_bridge_username: str = Field(default=...)


settings = Settings()
