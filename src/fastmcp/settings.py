from __future__ import annotations as _annotations

import inspect
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)
from typing_extensions import Self

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

    home: Path = Path.home() / ".fastmcp"

    test_mode: bool = False
    log_level: LOG_LEVEL = "INFO"
    enable_rich_tracebacks: Annotated[
        bool,
        Field(
            description=inspect.cleandoc(
                """
                If True, will use rich tracebacks for logging.
                """
            )
        ),
    ] = True

    client_raise_first_exceptiongroup_error: Annotated[
        bool,
        Field(
            default=True,
            description=inspect.cleandoc(
                """
                Many MCP components operate in anyio taskgroups, and raise
                ExceptionGroups instead of exceptions. If this setting is True, FastMCP Clients
                will `raise` the first error in any ExceptionGroup instead of raising
                the ExceptionGroup as a whole. This is useful for debugging, but may
                mask other errors.
                """
            ),
        ),
    ] = True

    resource_prefix_format: Annotated[
        Literal["protocol", "path"],
        Field(
            default="path",
            description=inspect.cleandoc(
                """
                When perfixing a resource URI, either use path formatting (resource://prefix/path)
                or protocol formatting (prefix+resource://path). Protocol formatting was the default in FastMCP < 2.4;
                path formatting is current default.
                """
            ),
        ),
    ] = "path"

    tool_attempt_parse_json_args: Annotated[
        bool,
        Field(
            default=False,
            description=inspect.cleandoc(
                """
                Note: this enables a legacy behavior. If True, will attempt to parse
                stringified JSON lists and objects strings in tool arguments before
                passing them to the tool. This is an old behavior that can create
                unexpected type coercion issues, but may be helpful for less powerful
                LLMs that stringify JSON instead of passing actual lists and objects.
                Defaults to False.
                """
            ),
        ),
    ] = False

    client_init_timeout: Annotated[
        float | None,
        Field(
            description="The timeout for the client's initialization handshake, in seconds. Set to None or 0 to disable.",
        ),
    ] = None

    @model_validator(mode="after")
    def setup_logging(self) -> Self:
        """Finalize the settings."""
        from fastmcp.utilities.logging import configure_logging

        configure_logging(
            self.log_level, enable_rich_tracebacks=self.enable_rich_tracebacks
        )

        return self


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

    log_level: Annotated[
        LOG_LEVEL,
        Field(default_factory=lambda: Settings().log_level),
    ]

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

    # error handling
    mask_error_details: Annotated[
        bool,
        Field(
            default=False,
            description=inspect.cleandoc(
                """
                If True, error details from user-supplied functions (tool, resource, prompt)
                will be masked before being sent to clients. Only error messages from explicitly
                raised ToolError, ResourceError, or PromptError will be included in responses.
                If False (default), all error details will be included in responses, but prefixed
                with appropriate context.
                """
            ),
        ),
    ] = False

    dependencies: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="List of dependencies to install in the server environment",
        ),
    ] = []

    # cache settings (for checking mounted servers)
    cache_expiration_seconds: float = 0

    # StreamableHTTP settings
    json_response: bool = False
    stateless_http: bool = (
        False  # If True, uses true stateless mode (new transport per request)
    )

    # Auth settings
    default_auth_provider: Annotated[
        Literal["bearer_env"] | None,
        Field(
            description=inspect.cleandoc(
                """
                Configure the authentication provider. This setting is intended only to
                be used for remote confirugation of providers that fully support
                environment variable configuration.

                If None, no automatic configuration will take place.

                This setting is *always* overriden by any auth provider passed to the
                FastMCP constructor.
                """
            ),
        ),
    ] = None


settings = Settings()
