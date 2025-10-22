from __future__ import annotations as _annotations

import inspect
import os
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from platformdirs import user_data_dir
from pydantic import Field, ImportString, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from typing_extensions import Self

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

ENV_FILE = os.getenv("FASTMCP_ENV_FILE", ".env")

LOG_LEVEL = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

DuplicateBehavior = Literal["warn", "error", "replace", "ignore"]

TEN_MB_IN_BYTES = 1024 * 1024 * 10

if TYPE_CHECKING:
    from fastmcp.server.auth.auth import AuthProvider


class ExtendedEnvSettingsSource(EnvSettingsSource):
    """
    A special EnvSettingsSource that allows for multiple env var prefixes to be used.

    Raises a deprecation warning if the old `FASTMCP_SERVER_` prefix is used.
    """

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        if prefixes := self.config.get("env_prefixes"):
            for prefix in prefixes:
                self.env_prefix = prefix
                env_val, field_key, value_is_complex = super().get_field_value(
                    field, field_name
                )
                if env_val is not None:
                    if prefix == "FASTMCP_SERVER_":
                        # Deprecated in 2.8.0
                        logger.warning(
                            "Using `FASTMCP_SERVER_` environment variables is deprecated. Use `FASTMCP_` instead.",
                        )
                    return env_val, field_key, value_is_complex

        return super().get_field_value(field, field_name)


class ExtendedSettingsConfigDict(SettingsConfigDict, total=False):
    env_prefixes: list[str] | None


class ExperimentalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_EXPERIMENTAL_",
        extra="ignore",
    )

    enable_new_openapi_parser: Annotated[
        bool,
        Field(
            description=inspect.cleandoc(
                """
                Whether to use the new OpenAPI parser. This parser was introduced
                for testing in 2.11 and will become the default soon.
                """
            ),
        ),
    ] = False


class Settings(BaseSettings):
    """FastMCP settings."""

    model_config = ExtendedSettingsConfigDict(
        env_prefixes=["FASTMCP_", "FASTMCP_SERVER_"],
        env_file=ENV_FILE,
        extra="ignore",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
        validate_assignment=True,
    )

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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # can remove this classmethod after deprecated FASTMCP_SERVER_ prefix is
        # removed
        return (
            init_settings,
            ExtendedEnvSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    @property
    def settings(self) -> Self:
        """
        This property is for backwards compatibility with FastMCP < 2.8.0,
        which accessed fastmcp.settings.settings
        """
        # Deprecated in 2.8.0
        logger.warning(
            "Using fastmcp.settings.settings is deprecated. Use fastmcp.settings instead.",
        )
        return self

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

    experimental: ExperimentalSettings = ExperimentalSettings()

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

    resource_prefix_format: Annotated[
        Literal["protocol", "path"],
        Field(
            description=inspect.cleandoc(
                """
                When perfixing a resource URI, either use path formatting (resource://prefix/path)
                or protocol formatting (prefix+resource://path). Protocol formatting was the default in FastMCP < 2.4;
                path formatting is current default.
                """
            ),
        ),
    ] = "path"

    client_init_timeout: Annotated[
        float | None,
        Field(
            description="The timeout for the client's initialization handshake, in seconds. Set to None or 0 to disable.",
        ),
    ] = None

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

    # Auth settings
    server_auth: Annotated[
        str | None,
        Field(
            description=inspect.cleandoc(
                """
                Configure the authentication provider for the server by specifying
                the full module path to an AuthProvider class (e.g., 
                'fastmcp.server.auth.providers.google.GoogleProvider').

                The specified class will be imported and instantiated automatically
                during FastMCP server creation. Any class that inherits from AuthProvider
                can be used, including custom implementations.

                If None, no automatic configuration will take place.

                This setting is *always* overridden by any auth provider passed to the
                FastMCP constructor.

                Note that most auth providers require additional configuration
                that must be provided via env vars.

                Examples:
                  - fastmcp.server.auth.providers.google.GoogleProvider
                  - fastmcp.server.auth.providers.jwt.JWTVerifier
                  - mycompany.auth.CustomAuthProvider
                """
            ),
        ),
    ] = None

    include_tags: Annotated[
        set[str] | None,
        Field(
            description=inspect.cleandoc(
                """
                If provided, only components that match these tags will be
                exposed to clients. A component is considered to match if ANY of
                its tags match ANY of the tags in the set.
                """
            ),
        ),
    ] = None
    exclude_tags: Annotated[
        set[str] | None,
        Field(
            description=inspect.cleandoc(
                """
                If provided, components that match these tags will be excluded
                from the server. A component is considered to match if ANY of
                its tags match ANY of the tags in the set.
                """
            ),
        ),
    ] = None

    include_fastmcp_meta: Annotated[
        bool,
        Field(
            description=inspect.cleandoc(
                """
                Whether to include FastMCP meta in the server's MCP responses.
                If True, a `_fastmcp` key will be added to the `meta` field of
                all MCP component responses. This key will contain a dict of
                various FastMCP-specific metadata, such as tags.
                """
            ),
        ),
    ] = True

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

    show_cli_banner: Annotated[
        bool,
        Field(
            description=inspect.cleandoc(
                """
                If True, the server banner will be displayed when running the server via CLI.
                This setting can be overridden by the --no-banner CLI flag.
                Set to False via FASTMCP_SHOW_CLI_BANNER=false to suppress the banner.
                """
            ),
        ),
    ] = True

    @property
    def server_auth_class(self) -> AuthProvider | None:
        from fastmcp.utilities.types import get_cached_typeadapter

        if not self.server_auth:
            return None

        # https://github.com/jlowin/fastmcp/issues/1749
        # Pydantic imports the module in an ImportString during model validation, but we don't want the server
        # auth module imported during settings creation as it imports dependencies we aren't ready for yet.
        # To fix this while limiting breaking changes, we delay the import by only creating the ImportString
        # when the class is actually needed

        type_adapter = get_cached_typeadapter(ImportString)

        auth_class = type_adapter.validate_python(self.server_auth)

        return auth_class


def __getattr__(name: str):
    """
    Used to deprecate the module-level Image class; can be removed once it is no longer imported to root.
    """
    if name == "settings":
        import fastmcp

        settings = fastmcp.settings
        # Deprecated in 2.10.2
        if settings.deprecation_warnings:
            warnings.warn(
                "`from fastmcp.settings import settings` is deprecated. use `fastmcp.settings` instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return settings

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
