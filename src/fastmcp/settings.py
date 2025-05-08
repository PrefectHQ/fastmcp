from __future__ import annotations as _annotations

from typing import TYPE_CHECKING, Literal

from mcp.server.auth.settings import AuthSettings
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    pass

LOG_LEVEL = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

DuplicateBehavior = Literal["warn", "error", "replace", "ignore"]


class Settings(BaseSettings):
    """FastMCP settings."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_",
        env_file=".env",
        extra="ignore",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
    )

    test_mode: bool = False
    log_level: LOG_LEVEL = "INFO"
    tool_attempt_parse_json_args: bool = Field(
        default=False,
        description="""
        Note: this enables a legacy behavior. If True, will attempt to parse
        stringified JSON lists and objects strings in tool arguments before
        passing them to the tool. This is an old behavior that can create
        unexpected type coercion issues, but may be helpful for less powerful
        LLMs that stringify JSON instead of passing actual lists and objects.
        Defaults to False.""",
    )


class ServerSettings(BaseSettings):
    """FastMCP server settings.

    All settings can be configured via environment variables with the prefix FASTMCP_.
    For example, FASTMCP_DEBUG=true will set debug=True.
    """

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_",
        env_file=".env",
        extra="ignore",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
    )

    log_level: LOG_LEVEL = Field(default_factory=lambda: Settings().log_level)

    # HTTP settings
    host: str = "127.0.0.1"
    port: int = 8000
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    streamable_http_path: str = "/mcp"
    debug: bool = False

    # resource settings
    on_duplicate_resources: DuplicateBehavior = "warn"

    # tool settings
    on_duplicate_tools: DuplicateBehavior = "warn"

    # prompt settings
    on_duplicate_prompts: DuplicateBehavior = "warn"

    dependencies: list[str] = Field(
        default_factory=list,
        description="List of dependencies to install in the server environment",
    )

    # cache settings (for checking mounted servers)
    cache_expiration_seconds: float = 0

    auth: AuthSettings | None = None

    # StreamableHTTP settings
    json_response: bool = False
    stateless_http: bool = (
        False  # If True, uses true stateless mode (new transport per request)
    )


class ClientSettings(BaseSettings):
    """FastMCP client settings."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_CLIENT_",
        env_file=".env",
        extra="ignore",
    )

    log_level: LOG_LEVEL = Field(default_factory=lambda: Settings().log_level)


settings = Settings()
