from __future__ import annotations as _annotations

import inspect
import os
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from platformdirs import user_data_dir
from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources.providers.env import EnvSettingsSource

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

ENV_FILE = os.getenv("FASTMCP_ENV_FILE", ".env")

LOG_LEVEL = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

DuplicateBehavior = Literal["warn", "error", "replace", "ignore"]

TEN_MB_IN_BYTES = 1024 * 1024 * 10


class DocketSettings(BaseSettings):
    """Docket worker configuration."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_DOCKET_",
        extra="ignore",
    )

    name: Annotated[
        str,
        Field(
            description=inspect.cleandoc(
                """
                Name for the Docket queue. All servers/workers sharing the same name
                and backend URL will share a task queue.
                """
            ),
        ),
    ] = "fastmcp"

    url: Annotated[
        str,
        Field(
            description=inspect.cleandoc(
                """
                URL for the Docket backend. Supports:
                - memory:// - In-memory backend (single process only)
                - redis://host:port/db - Redis/Valkey backend (distributed, multi-process)

                Example: redis://localhost:6379/0

                Default is memory:// for single-process scenarios. Use Redis or Valkey
                when coordinating tasks across multiple processes (e.g., additional
                workers via the fastmcp tasks CLI).
                """
            ),
        ),
    ] = "memory://"

    worker_name: Annotated[
        str | None,
        Field(
            description=inspect.cleandoc(
                """
                Name for the Docket worker. If None, Docket will auto-generate
                a unique worker name.
                """
            ),
        ),
    ] = None

    concurrency: Annotated[
        int,
        Field(
            description=inspect.cleandoc(
                """
                Maximum number of tasks the worker can process concurrently.
                """
            ),
        ),
    ] = 10

    redelivery_timeout: Annotated[
        timedelta,
        Field(
            description=inspect.cleandoc(
                """
                Task redelivery timeout. If a worker doesn't complete
                a task within this time, the task will be redelivered to another
                worker.
                """
            ),
        ),
    ] = timedelta(seconds=300)

    reconnection_delay: Annotated[
        timedelta,
        Field(
            description=inspect.cleandoc(
                """
                Delay between reconnection attempts when the worker
                loses connection to the Docket backend.
                """
            ),
        ),
    ] = timedelta(seconds=5)


def _inject_prefix_aliases(source: PydanticBaseSettingsSource) -> None:
    """Add ``<PREFIX>_<FIELD>`` aliases for env vars written as ``<PREFIX>__<FIELD>``.

    Many projects separate a namespace prefix from a field name with a
    double underscore (``DATABASE__PASSWORD``, ``COGNITO__CLIENT_SECRET``).
    With ``env_prefix="FASTMCP_"`` + ``env_nested_delimiter="__"``,
    pydantic-settings parses ``FASTMCP__HOME`` as field ``_home`` (which
    doesn't exist) and silently drops it — only ``FASTMCP_HOME`` is seen.

    For each ``FASTMCP__<FIELD>`` entry in the source's already-loaded
    ``env_vars``, write the canonical ``FASTMCP_<FIELD>`` key (via
    ``setdefault``, so an explicit canonical value wins). Nested fields
    are unaffected because this only translates the namespace boundary:
    ``FASTMCP__DOCKET__NAME`` becomes ``FASTMCP_DOCKET__NAME`` which
    pydantic then splits on ``__`` as usual.
    """
    if not isinstance(source, EnvSettingsSource):
        return  # Not an env-backed source — nothing to translate.
    prefix = source.env_prefix if source.case_sensitive else source.env_prefix.lower()
    alias = prefix + "_"
    # Upgrade to a mutable dict — some sources expose a read-only Mapping
    # and `get_field_value` just reads from this attribute either way.
    env_vars: dict[str, str | None] = dict(source.env_vars)
    for key in list(env_vars):
        if key.startswith(alias):
            canonical = prefix + key[len(alias) :]
            env_vars.setdefault(canonical, env_vars[key])
    source.env_vars = env_vars


class Settings(BaseSettings):
    """FastMCP settings."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_",
        env_file=ENV_FILE,
        extra="ignore",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
        validate_assignment=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Accept ``FASTMCP__<FIELD>`` aliases on the env + dotenv sources.

        Mutating the sources in place is cleaner than substituting
        subclasses — it preserves any init-time overrides (e.g.
        ``Settings(_env_file=...)``) that pydantic-settings has already
        baked into the default source instances.
        """
        _inject_prefix_aliases(env_settings)
        _inject_prefix_aliases(dotenv_settings)
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    def get_setting(self, attr: str) -> Any:
        """
        Get a setting. If the setting contains one or more `__`, it will be
        treated as a nested setting.
        """
        settings = self
        while "__" in attr:
            parent_attr, attr = attr.split("__", 1)
            if not hasattr(settings, parent_attr):
                raise AttributeError(f"Setting {parent_attr} does not exist.")
            settings = getattr(settings, parent_attr)
        return getattr(settings, attr)

    def set_setting(self, attr: str, value: Any) -> None:
        """
        Set a setting. If the setting contains one or more `__`, it will be
        treated as a nested setting.
        """
        settings = self
        while "__" in attr:
            parent_attr, attr = attr.split("__", 1)
            if not hasattr(settings, parent_attr):
                raise AttributeError(f"Setting {parent_attr} does not exist.")
            settings = getattr(settings, parent_attr)
        setattr(settings, attr, value)

    home: Path = Path(user_data_dir("fastmcp", appauthor=False))

    test_mode: bool = False

    log_enabled: bool = True
    log_level: LOG_LEVEL = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, v):
        if isinstance(v, str):
            return v.upper()
        return v

    docket: DocketSettings = DocketSettings()

    enable_rich_logging: Annotated[
        bool,
        Field(
            description=inspect.cleandoc(
                """
                If True, will use rich formatting for log output. If False,
                will use standard Python logging without rich formatting.
                """
            )
        ),
    ] = True

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

    deprecation_warnings: Annotated[
        bool,
        Field(
            description=inspect.cleandoc(
                """
                Whether to show deprecation warnings. You can completely reset
                Python's warning behavior by running `warnings.resetwarnings()`.
                Note this will NOT apply to deprecation warnings from the
                settings class itself.
                """,
            )
        ),
    ] = True

    client_raise_first_exceptiongroup_error: Annotated[
        bool,
        Field(
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

    client_init_timeout: Annotated[
        float | None,
        Field(
            description="The timeout for the client's initialization handshake, in seconds. Set to None or 0 to disable.",
        ),
    ] = None

    # Transport settings
    transport: Literal["stdio", "http", "sse", "streamable-http"] = "stdio"

    # HTTP settings
    host: str = "127.0.0.1"
    port: int = 8000
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    streamable_http_path: str = "/mcp"
    debug: bool = False

    # error handling
    mask_error_details: Annotated[
        bool,
        Field(
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

    strict_input_validation: Annotated[
        bool,
        Field(
            description=inspect.cleandoc(
                """
                If True, tool inputs are strictly validated against the input
                JSON schema. For example, providing the string \"10\" to an
                integer field will raise an error. If False, compatible inputs
                will be coerced to match the schema, which can increase
                compatibility. For example, providing the string \"10\" to an
                integer field will be coerced to 10. Defaults to False.
                """
            ),
        ),
    ] = False

    server_dependencies: list[str] = Field(
        default_factory=list,
        description="List of dependencies to install in the server environment",
    )

    # StreamableHTTP settings
    json_response: bool = False
    stateless_http: bool = (
        False  # If True, uses true stateless mode (new transport per request)
    )

    mounted_components_raise_on_load_error: Annotated[
        bool,
        Field(
            description=inspect.cleandoc(
                """
                If True, errors encountered when loading mounted components (tools, resources, prompts)
                will be raised instead of logged as warnings. This is useful for debugging
                but will interrupt normal operation.
                """
            ),
        ),
    ] = False

    show_server_banner: Annotated[
        bool,
        Field(
            description=inspect.cleandoc(
                """
                If True, the server banner will be displayed when running the server.
                This setting can be overridden by the --no-banner CLI flag or by
                passing show_banner=False to server.run().
                Set to False via FASTMCP_SHOW_SERVER_BANNER=false to suppress the banner.
                """
            ),
        ),
    ] = True

    check_for_updates: Annotated[
        Literal["stable", "prerelease", "off"],
        Field(
            description=inspect.cleandoc(
                """
                Controls update checking when displaying the CLI banner.
                - "stable": Check for stable releases only (default)
                - "prerelease": Also check for pre-release versions (alpha, beta, rc)
                - "off": Disable update checking entirely
                Set via FASTMCP_CHECK_FOR_UPDATES environment variable.
                """
            ),
        ),
    ] = "stable"

    decorator_mode: Annotated[
        Literal["function", "object"],
        Field(
            description=inspect.cleandoc(
                """
                Controls what decorators (@tool, @resource, @prompt) return.

                - "function" (default): Decorators return the original function unchanged.
                  The function remains callable and is registered with the server normally.
                - "object" (deprecated): Decorators return component objects (FunctionTool,
                  FunctionResource, FunctionPrompt). This was the default behavior in v2 and
                  will be removed in a future version.
                """
            ),
        ),
    ] = "function"
