from __future__ import annotations as _annotations

from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    pass

LOG_LEVEL = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class DuplicateBehavior(Enum):
    WARN = "warn"
    ERROR = "error"
    REPLACE = "replace"
    IGNORE = "ignore"


class Settings(BaseSettings):
    """FastMCP settings."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_",
        env_file=".env",
        extra="ignore",
    )

    test_mode: bool = False
    log_level: LOG_LEVEL = "INFO"


class ServerSettings(BaseSettings):
    """FastMCP server settings.

    All settings can be configured via environment variables with the prefix FASTMCP_.
    For example, FASTMCP_DEBUG=true will set debug=True.
    """

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_",
        env_file=".env",
        extra="ignore",
    )

    log_level: LOG_LEVEL = Field(default_factory=lambda: Settings().log_level)

    # HTTP settings
    host: str = "0.0.0.0"
    port: int = 8000
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    debug: bool = False

    # resource settings
    on_duplicate_resources: DuplicateBehavior = DuplicateBehavior.WARN

    # tool settings
    on_duplicate_tools: DuplicateBehavior = DuplicateBehavior.WARN

    # prompt settings
    on_duplicate_prompts: DuplicateBehavior = DuplicateBehavior.WARN

    dependencies: list[str] = Field(
        default_factory=list,
        description="List of dependencies to install in the server environment",
    )


class ClientSettings(BaseSettings):
    """FastMCP client settings."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_CLIENT_",
        env_file=".env",
        extra="ignore",
    )

    log_level: LOG_LEVEL = Field(default_factory=lambda: Settings().log_level)
