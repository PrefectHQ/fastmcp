from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    hue_bridge_ip: str
    hue_bridge_username: str
    hue_bridge_certificate: Path | None = None
