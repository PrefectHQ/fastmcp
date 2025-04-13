from pydantic import Field, IPvAnyAddress
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    hue_bridge_ip: IPvAnyAddress = Field(default=...)


settings = Settings()
