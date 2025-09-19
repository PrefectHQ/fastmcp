"""FastMCP Configuration File Support.

This module provides support for fastmcp.json configuration files that allow
users to specify server settings in a declarative format instead of using
command-line arguments.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast, overload

from pydantic import BaseModel, Field, field_validator

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment
from fastmcp.utilities.mcp_server_config.v1.sources.base import Source
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource

logger = get_logger("cli.config")

# JSON Schema for IDE support
FASTMCP_JSON_SCHEMA = "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json"


# Type alias for source union (will expand with GitSource, etc. in future)
SourceType: TypeAlias = FileSystemSource

# Type alias for environment union (will expand with other environments in future)
EnvironmentType: TypeAlias = UVEnvironment


class Deployment(BaseModel):
    """Configuration for server deployment and runtime settings."""

    transport: Literal["stdio", "http", "sse", "streamable-http"] | None = Field(
        default=None,
        description="Transport protocol to use",
    )

    host: str | None = Field(
        default=None,
        description="Host to bind to when using HTTP transport",
        examples=["127.0.0.1", "0.0.0.0", "localhost"],
    )

    port: int | None = Field(
        default=None,
        description="Port to bind to when using HTTP transport",
        examples=[8000, 3000, 5000],
    )

    path: str | None = Field(
        default=None,
        description="URL path for the server endpoint",
        examples=["/mcp/", "/api/mcp/", "/sse/"],
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = Field(
        default=None,
        description="Log level for the server",
    )

    cwd: str | None = Field(
        default=None,
        description="Working directory for the server process",
        examples=[".", "./src", "/app"],
    )

    env: dict[str, str] | None = Field(
        default=None,
        description="Environment variables to set when running the server",
        examples=[{"API_KEY": "secret", "DEBUG": "true"}],
    )

    args: list[str] | None = Field(
        default=None,
        description="Arguments to pass to the server (after --)",
        examples=[["--config", "config.json", "--debug"]],
    )

    def apply_runtime_settings(self, config_path: Path | None = None) -> None:
        """Apply runtime settings like environment variables and working directory.

        Args:
            config_path: Path to config file for resolving relative paths

        Environment variables support interpolation with ${VAR_NAME} syntax.
        For example: "API_URL": "https://api.${ENVIRONMENT}.example.com"
        will substitute the value of the ENVIRONMENT variable at runtime.
        """
        import os
        from pathlib import Path

        # Set environment variables with interpolation support
        if self.env:
            for key, value in self.env.items():
                # Interpolate environment variables in the value
                interpolated_value = self._interpolate_env_vars(value)
                os.environ[key] = interpolated_value

        # Change working directory
        if self.cwd:
            cwd_path = Path(self.cwd)
            if not cwd_path.is_absolute() and config_path:
                cwd_path = (config_path.parent / cwd_path).resolve()
            os.chdir(cwd_path)

    def _interpolate_env_vars(self, value: str) -> str:
        """Interpolate environment variables in a string.

        Replaces ${VAR_NAME} with the value of VAR_NAME from the environment.
        If the variable is not set, the placeholder is left unchanged.

        Args:
            value: String potentially containing ${VAR_NAME} placeholders

        Returns:
            String with environment variables interpolated
        """

        def replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            # Return the environment variable value if it exists, otherwise keep the placeholder
            return os.environ.get(var_name, match.group(0))

        # Match ${VAR_NAME} pattern and replace with environment variable values
        return re.sub(r"\$\{([^}]+)\}", replace_var, value)


class MCPServerConfig(BaseModel):
    """Configuration for a FastMCP server.

    This configuration file allows you to specify all settings needed to run
    a FastMCP server in a declarative format.
    """

    # Schema field for IDE support
    schema_: str | None = Field(
        default="https://gofastmcp.com/public/schemas/fastmcp.json/v1.json",
        alias="$schema",
        description="JSON schema for IDE support and validation",
    )

    # Server source - defines where and how to load the server
    source: SourceType = Field(
        description="Source configuration for the server",
        examples=[
            {"path": "server.py"},
            {"path": "server.py", "entrypoint": "app"},
            {"type": "filesystem", "path": "src/server.py", "entrypoint": "mcp"},
        ],
    )

    # Environment configuration
    environment: EnvironmentType = Field(
        default_factory=lambda: UVEnvironment(),
        description="Python environment setup configuration",
    )

    # Deployment configuration
    deployment: Deployment = Field(
        default_factory=lambda: Deployment(),
        description="Server deployment and runtime settings",
    )

    # purely for static type checkers to avoid issues with providing dict source
    if TYPE_CHECKING:

        @overload
        def __init__(self, *, source: dict | FileSystemSource, **data) -> None: ...
        @overload
        def __init__(self, *, environment: dict | UVEnvironment, **data) -> None: ...
        @overload
        def __init__(self, *, deployment: dict | Deployment, **data) -> None: ...
        def __init__(self, **data) -> None: ...

    @field_validator("source", mode="before")
    @classmethod
    def validate_source(cls, v: dict | Source) -> SourceType:
        """Validate and convert source to proper format.

        Supports:
        - Dict format: `{"path": "server.py", "entrypoint": "app"}`
        - FileSystemSource instance (passed through)

        No string parsing happens here - that's only at CLI boundaries.
        MCPServerConfig works only with properly typed objects.
        """
        if isinstance(v, dict):
            return FileSystemSource(**v)
        return v

    @field_validator("environment", mode="before")
    @classmethod
    def validate_environment(cls, v: dict | Any) -> EnvironmentType:
        """Ensure environment has a type field for discrimination.

        For backward compatibility, if no type is specified, default to "uv".
        """
        if isinstance(v, dict):
            return UVEnvironment(**v)
        return v

    @field_validator("deployment", mode="before")
    @classmethod
    def validate_deployment(cls, v: dict | Deployment) -> Deployment:
        """Validate and convert deployment to Deployment.

        Accepts:
        - Deployment instance
        - dict that can be converted to Deployment

        """
        if isinstance(v, dict):
            return Deployment(**v)  # type: ignore[arg-type]
        return cast(Deployment, v)

    @classmethod
    def from_file(cls, file_path: Path) -> MCPServerConfig:
        """Load configuration from a JSON file.

        Args:
            file_path: Path to the configuration file

        Returns:
            MCPServerConfig instance

        Raises:
            FileNotFoundError: If the file doesn't exist
            json.JSONDecodeError: If the file is not valid JSON
            pydantic.ValidationError: If the configuration is invalid
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return cls.model_validate(data)

    @classmethod
    def from_cli_args(
        cls,
        source: FileSystemSource,
        transport: Literal["stdio", "http", "sse", "streamable-http"] | None = None,
        host: str | None = None,
        port: int | None = None,
        path: str | None = None,
        log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        | None = None,
        python: str | None = None,
        dependencies: list[str] | None = None,
        requirements: str | None = None,
        project: str | None = None,
        editable: str | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        args: list[str] | None = None,
    ) -> MCPServerConfig:
        """Create a config from CLI arguments.

        This allows us to have a single code path where everything
        goes through a config object.

        Args:
            source: Server source (FileSystemSource instance)
            transport: Transport protocol
            host: Host for HTTP transport
            port: Port for HTTP transport
            path: URL path for server
            log_level: Logging level
            python: Python version
            dependencies: Python packages to install
            requirements: Path to requirements file
            project: Path to project directory
            editable: Path to install in editable mode
            env: Environment variables
            cwd: Working directory
            args: Server arguments

        Returns:
            MCPServerConfig instance
        """
        # Build environment config if any env args provided
        environment = None
        if any([python, dependencies, requirements, project, editable]):
            environment = UVEnvironment(
                python=python,
                dependencies=dependencies,
                requirements=requirements,
                project=project,
                editable=[editable] if editable else None,
            )

        # Build deployment config if any deployment args provided
        deployment = None
        if any([transport, host, port, path, log_level, env, cwd, args]):
            # Convert streamable-http to http for backward compatibility
            if transport == "streamable-http":
                transport = "http"  # type: ignore[assignment]
            deployment = Deployment(
                transport=transport,  # type: ignore[arg-type]
                host=host,
                port=port,
                path=path,
                log_level=log_level,
                env=env,
                cwd=cwd,
                args=args,
            )

        return cls(
            source=source,
            environment=environment,
            deployment=deployment,
        )

    @classmethod
    def find_config(cls, start_path: Path | None = None) -> Path | None:
        """Find a fastmcp.json file in the specified directory.

        Args:
            start_path: Directory to look in (defaults to current directory)

        Returns:
            Path to the configuration file, or None if not found
        """
        if start_path is None:
            start_path = Path.cwd()

        config_path = start_path / "fastmcp.json"
        if config_path.exists():
            logger.debug(f"Found configuration file: {config_path}")
            return config_path

        return None

    async def prepare(
        self,
        skip_source: bool = False,
        output_dir: Path | None = None,
    ) -> None:
        """Prepare environment and source for execution.

        When output_dir is provided, creates a persistent uv project.
        When output_dir is None, does ephemeral caching (for backwards compatibility).

        Args:
            skip_source: Skip source preparation if True
            output_dir: Directory to create the persistent uv project in (optional)
        """
        # Prepare environment (persistent if output_dir provided, ephemeral otherwise)
        if self.environment:
            await self.prepare_environment(output_dir=output_dir)

        if not skip_source:
            await self.prepare_source()

    async def prepare_environment(self, output_dir: Path | None = None) -> None:
        """Prepare the Python environment.

        Args:
            output_dir: If provided, creates a persistent uv project in this directory.
                       If None, just populates uv's cache for ephemeral use.

        Delegates to the environment's prepare() method
        """
        await self.environment.prepare(output_dir=output_dir)

    async def prepare_source(self) -> None:
        """Prepare the source for loading.

        Delegates to the source's prepare() method.
        """
        await self.source.prepare()

    async def run_server(self, **kwargs: Any) -> None:
        """Load and run the server with this configuration.

        Args:
            **kwargs: Additional arguments to pass to server.run_async()
                     These override config settings
        """
        # Apply deployment settings (env vars, cwd)
        if self.deployment:
            self.deployment.apply_runtime_settings()

        # Load the server
        server = await self.source.load_server()

        # Build run arguments from config
        run_args = {}
        if self.deployment:
            if self.deployment.transport:
                run_args["transport"] = self.deployment.transport
            if self.deployment.host:
                run_args["host"] = self.deployment.host
            if self.deployment.port:
                run_args["port"] = self.deployment.port
            if self.deployment.path:
                run_args["path"] = self.deployment.path
            if self.deployment.log_level:
                run_args["log_level"] = self.deployment.log_level

        # Override with any provided kwargs
        run_args.update(kwargs)

        # Run the server
        await server.run_async(**run_args)


def generate_schema(output_path: Path | str | None = None) -> dict[str, Any] | None:
    """Generate JSON schema for fastmcp.json files.

    This is used to create the schema file that IDEs can use for
    validation and auto-completion.

    Args:
        output_path: Optional path to write the schema to. If provided,
                    writes the schema and returns None. If not provided,
                    returns the schema as a dictionary.

    Returns:
        JSON schema as a dictionary if output_path is None, otherwise None
    """
    schema = MCPServerConfig.model_json_schema()

    # Add some metadata
    schema["$id"] = FASTMCP_JSON_SCHEMA
    schema["title"] = "FastMCP Configuration"
    schema["description"] = "Configuration file for FastMCP servers"

    if output_path:
        import json

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w") as f:
            json.dump(schema, f, indent=2)
            f.write("\n")  # Add trailing newline
        return None

    return schema
