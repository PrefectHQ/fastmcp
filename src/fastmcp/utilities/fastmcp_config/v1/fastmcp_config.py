"""FastMCP Configuration File Support.

This module provides support for fastmcp.json configuration files that allow
users to specify server settings in a declarative format instead of using
command-line arguments.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

from pydantic import BaseModel, Field, field_validator

from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.config")

# JSON Schema for IDE support
FASTMCP_JSON_SCHEMA = "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json"


class BaseSource(BaseModel, ABC):
    """Abstract base class for all source types."""

    type: str = Field(description="Source type identifier")

    async def prepare(self, config_path: Path | None = None) -> Path | None:
        """Prepare the source (download, clone, install, etc).

        Returns:
            Path to prepared source directory, or None if no preparation needed.
            This path may contain a nested fastmcp.json for configuration chaining.
        """
        # Default implementation for sources that don't need preparation
        return None

    @abstractmethod
    async def load_server(
        self, config_path: Path | None = None, server_args: list[str] | None = None
    ) -> Any:
        """Load and return the FastMCP server instance.

        Must be called after prepare() if the source requires preparation.
        """
        ...


class FileSystemSource(BaseSource):
    """Source for local Python files."""

    type: Literal["filesystem"] = Field(default="filesystem", description="Source type")
    path: str = Field(description="Path to Python file containing the server")
    entrypoint: str | None = Field(
        default=None,
        description="Name of server instance or factory function (a no-arg function that returns a FastMCP server)",
    )

    async def load_server(
        self, config_path: Path | None = None, server_args: list[str] | None = None
    ) -> Any:
        """Load server from filesystem."""
        from fastmcp.cli.run import import_server_with_args

        # Resolve relative paths if config_path provided
        file_path = Path(self.path)
        if not file_path.is_absolute() and config_path:
            file_path = (config_path.parent / file_path).resolve()

        return await import_server_with_args(file_path, self.entrypoint, server_args)


# Type alias for source union (will expand with GitSource, etc in future)
SourceType = FileSystemSource


class Environment(BaseModel):
    """Configuration for Python environment setup."""

    python: str | None = Field(
        default=None,
        description="Python version constraint",
        examples=["3.10", "3.11", "3.12"],
    )

    dependencies: list[str] | None = Field(
        default=None,
        description="Python packages to install with PEP 508 specifiers",
        examples=[["fastmcp>=2.0,<3", "httpx", "pandas>=2.0"]],
    )

    requirements: str | None = Field(
        default=None,
        description="Path to requirements.txt file",
        examples=["requirements.txt", "../requirements/prod.txt"],
    )

    project: str | None = Field(
        default=None,
        description="Path to project directory containing pyproject.toml",
        examples=[".", "../my-project"],
    )

    editable: str | None = Field(
        default=None,
        description="Directory to install in editable mode",
        examples=[".", "../my-package"],
    )

    def build_uv_args(self, command: str | list[str] | None = None) -> list[str]:
        """Build uv run arguments from this environment configuration.

        Args:
            command: Optional command to append (string or list of args)

        Returns:
            List of arguments for uv run command
        """
        args = ["run"]

        # Add Python version if specified
        if self.python:
            args.extend(["--python", self.python])

        # Add project directory if specified
        if self.project:
            args.extend(["--project", str(self.project)])

        # Add fastmcp as a base dependency
        args.extend(["--with", "fastmcp"])

        # Add additional dependencies (skip fastmcp if already added)
        if self.dependencies:
            for dep in self.dependencies:
                if dep != "fastmcp":  # Skip fastmcp since we already added it
                    args.extend(["--with", dep])

        # Add requirements file
        if self.requirements:
            args.extend(["--with-requirements", str(self.requirements)])

        # Add editable package
        if self.editable:
            args.extend(["--with-editable", str(self.editable)])

        # Add the command if provided
        if command:
            if isinstance(command, str):
                args.append(command)
            else:
                args.extend(command)

        return args

    def run_with_uv(self, command: list[str]) -> None:
        """Execute a command using uv run with this environment configuration.

        Args:
            command: Command and arguments to execute (e.g., ["fastmcp", "run", "server.py"])
        """
        import subprocess
        import sys

        # Build the full uv command
        uv_args = self.build_uv_args(command)
        cmd = ["uv"] + uv_args

        logger.debug(f"Running command: {' '.join(cmd)}")

        try:
            # Run without capturing output so it flows through naturally
            process = subprocess.run(cmd, check=True)
            sys.exit(process.returncode)
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {e}")
            sys.exit(e.returncode)

    def needs_uv(self) -> bool:
        """Check if this environment config requires uv to set up.

        Returns:
            True if any environment settings require uv run
        """
        return any(
            [
                self.python is not None,
                self.dependencies is not None,
                self.requirements is not None,
                self.project is not None,
                self.editable is not None,
            ]
        )


class Deployment(BaseModel):
    """Configuration for server deployment and runtime settings."""

    transport: Literal["stdio", "http", "sse"] | None = Field(
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


class FastMCPConfig(BaseModel):
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
    environment: Environment = Field(
        default_factory=lambda: Environment(),
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
        def __init__(self, *, environment: dict | Environment, **data) -> None: ...
        @overload
        def __init__(self, *, deployment: dict | Deployment, **data) -> None: ...
        def __init__(self, **data) -> None: ...

    @field_validator("source", mode="before")
    @classmethod
    def validate_source(cls, v: dict | FileSystemSource) -> FileSystemSource:
        """Validate and convert source to proper format.

        Supports:
        - Dict format: {"path": "server.py", "entrypoint": "app"}
        - FileSystemSource instance (passed through)

        No string parsing happens here - that's only at CLI boundaries.
        FastMCPConfig works only with properly typed objects.
        """
        if isinstance(v, FileSystemSource):
            # Already a FileSystemSource instance, return as-is
            return v
        elif isinstance(v, dict):
            # Dict can have type field or not (filesystem is default)
            if "type" not in v:
                v["type"] = "filesystem"
            return FileSystemSource(**v)
        else:
            raise ValueError("source must be a dict or FileSystemSource instance")

    @field_validator("environment", mode="before")
    @classmethod
    def validate_environment(cls, v: dict | Environment) -> Environment:
        """Validate and convert environment to Environment.

        Accepts:
        - Environment instance
        - dict that can be converted to Environment
        """
        if isinstance(v, Environment):
            return v
        elif isinstance(v, dict):
            return Environment(**v)  # type: ignore[arg-type]
        else:
            raise ValueError("environment must be a dict, Environment instance")

    @field_validator("deployment", mode="before")
    @classmethod
    def validate_deployment(cls, v: dict | Deployment) -> Deployment:
        """Validate and convert deployment to Deployment.

        Accepts:
        - Deployment instance
        - dict that can be converted to Deployment

        """
        if isinstance(v, Deployment):
            return v
        elif isinstance(v, dict):
            return Deployment(**v)  # type: ignore[arg-type]
        else:
            raise ValueError("deployment must be a dict, Deployment instance")

    @classmethod
    def from_file(cls, file_path: Path) -> FastMCPConfig:
        """Load configuration from a JSON file.

        Args:
            file_path: Path to the configuration file

        Returns:
            FastMCPConfig instance

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
    ) -> FastMCPConfig:
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
            FastMCPConfig instance
        """
        # Build environment config if any env args provided
        environment = None
        if any([python, dependencies, requirements, project, editable]):
            environment = Environment(
                python=python,
                dependencies=dependencies,
                requirements=requirements,
                project=project,
                editable=editable,
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

    async def load_server(
        self, config_path: Path | None = None, server_args: list[str] | None = None
    ) -> Any:
        """Load the server from the configuration.

        This handles environment setup, working directory changes,
        and delegates to the source's load_server method.

        Args:
            config_path: Path to the config file (for resolving relative paths)
            server_args: Optional arguments to pass to the server

        Returns:
            The imported server object
        """
        import os
        from pathlib import Path

        # Set environment variables if specified
        if self.deployment and self.deployment.env:
            for key, value in self.deployment.env.items():
                os.environ[key] = value

        # Change working directory if specified
        if self.deployment and self.deployment.cwd:
            cwd_path = Path(self.deployment.cwd)
            if not cwd_path.is_absolute():
                # If config_path provided, resolve relative to it
                if config_path:
                    cwd_path = (config_path.parent / cwd_path).resolve()
                else:
                    cwd_path = cwd_path.resolve()
            os.chdir(cwd_path)

        # Use server_args from deployment if not provided
        if server_args is None and self.deployment:
            server_args = self.deployment.args

        # Delegate to the source's load_server method
        return await self.source.load_server(config_path, server_args)

    async def run_server(self, **kwargs: Any) -> None:
        """Load and run the server with this configuration.

        Args:
            **kwargs: Additional arguments to pass to server.run_async()
                     These override config settings
        """
        server = await self.load_server()

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
            # Note: log_level not currently supported by run_async

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
    schema = FastMCPConfig.model_json_schema()

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
