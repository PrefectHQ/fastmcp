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
from typing import TYPE_CHECKING, Any, Literal, overload

from pydantic import BaseModel, Field, field_validator

from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.config")

# JSON Schema for IDE support
FASTMCP_JSON_SCHEMA = "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json"


class EntrypointConfig(BaseModel):
    """Configuration for server entrypoint when using object format."""

    file: str = Field(
        description="Path to Python file containing the server",
        examples=["server.py", "src/server.py", "app/main.py"],
    )

    object: str | None = Field(
        default=None,
        description="Name of the server object in the file (defaults to searching for mcp/server/app)",
        examples=["app", "mcp", "server"],
    )

    repo: str | None = Field(
        default=None,
        description="Git repository URL",
        examples=["https://github.com/user/repo"],
    )


class EnvironmentConfig(BaseModel):
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

        # Add additional dependencies
        if self.dependencies:
            for dep in self.dependencies:
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
        return bool(
            self.python
            or self.dependencies
            or self.requirements
            or self.project
            or self.editable
        )

    def merge_with_cli_args(
        self,
        python: str | None = None,
        with_packages: list[str] | None = None,
        with_requirements: Path | None = None,
        project: Path | None = None,
        with_editable: Path | None = None,
    ) -> dict[str, Any]:
        """Merge environment config with CLI arguments, with CLI taking precedence.

        For packages, combines both config and CLI packages.
        For other fields, CLI takes precedence if provided.

        Returns:
            Dictionary with merged arguments suitable for CLI commands
        """
        from pathlib import Path

        # Merge packages from both sources
        packages = []
        if self.dependencies:
            packages.extend(self.dependencies)
        if with_packages:
            packages.extend(with_packages)

        return {
            "python": python or self.python,
            "with_packages": packages,
            "with_requirements": with_requirements
            or (Path(self.requirements) if self.requirements else None),
            "project": project or (Path(self.project) if self.project else None),
            "with_editable": with_editable
            or (Path(self.editable) if self.editable else None),
        }


class DeploymentConfig(BaseModel):
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

    def merge_with_cli_args(
        self,
        transport: str | None = None,
        host: str | None = None,
        port: int | None = None,
        path: str | None = None,
        log_level: str | None = None,
        server_args: list[str] | None = None,
    ) -> dict[str, Any]:
        """Merge deployment config with CLI arguments, with CLI taking precedence.

        Returns:
            Dictionary with merged arguments suitable for CLI commands
        """
        return {
            "transport": transport or self.transport,
            "host": host or self.host,
            "port": port or self.port,
            "path": path or self.path,
            "log_level": log_level or self.log_level,
            "server_args": server_args if server_args is not None else self.args,
        }

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

    # Server entrypoint - supports both string and object format
    entrypoint: EntrypointConfig = Field(
        description="Server entrypoint as a string (file or file:object) or object with file/object/repo",
        examples=[
            "server.py",
            "server.py:app",
            {"file": "src/server.py", "object": "app"},
        ],
    )

    # Environment configuration
    environment: EnvironmentConfig = Field(
        default_factory=lambda: EnvironmentConfig(),
        description="Python environment setup configuration",
    )

    # Deployment configuration
    deployment: DeploymentConfig = Field(
        default_factory=lambda: DeploymentConfig(),
        description="Server deployment and runtime settings",
    )

    # purely for static type checkers to avoid issues with providng str entrypoint
    if TYPE_CHECKING:

        @overload
        def __init__(
            self, *, entrypoint: str | dict | EntrypointConfig, **data
        ) -> None: ...
        @overload
        def __init__(
            self, *, environment: dict | EnvironmentConfig, **data
        ) -> None: ...
        @overload
        def __init__(self, *, deployment: dict | DeploymentConfig, **data) -> None: ...
        def __init__(self, **data) -> None: ...

    @field_validator("entrypoint", mode="before")
    @classmethod
    def validate_entrypoint(cls, v: str | EntrypointConfig) -> EntrypointConfig:
        """Validate and convert entrypoint to proper format.

        Supports:
        - String format: "server.py" or "server.py:object"
        - Object format: {"file": "server.py", "object": "app"}
        - EntrypointConfig instance (passed through)

        The string format with :object syntax is automatically parsed into
        the object format for consistency.
        """
        if isinstance(v, EntrypointConfig):
            # Already an EntrypointConfig instance, return as-is
            return v
        elif isinstance(v, dict):
            return EntrypointConfig(**v)
        elif isinstance(v, str):
            # Parse file.py:object syntax into object format if present
            if ":" in v:
                # Check if it's a Windows path (e.g., C:\...)
                has_windows_drive = len(v) > 1 and v[1] == ":"

                # Only split if colon is not part of Windows drive
                if ":" in (v[2:] if has_windows_drive else v):
                    file, obj = v.rsplit(":", 1)
                    return EntrypointConfig(file=file, object=obj)
            else:
                return EntrypointConfig(file=v)

        raise ValueError("entrypoint must be a string, EntrypointConfig instance")

    @field_validator("environment", mode="before")
    @classmethod
    def validate_environment(cls, v: dict | EnvironmentConfig) -> EnvironmentConfig:
        """Validate and convert environment to EnvironmentConfig.

        Accepts:
        - EnvironmentConfig instance
        - dict that can be converted to EnvironmentConfig
        """
        if isinstance(v, EnvironmentConfig):
            return v
        elif isinstance(v, dict):
            return EnvironmentConfig(**v)  # type: ignore[arg-type]
        else:
            raise ValueError("environment must be a dict, EnvironmentConfig instance")

    @field_validator("deployment", mode="before")
    @classmethod
    def validate_deployment(cls, v: dict | DeploymentConfig) -> DeploymentConfig:
        """Validate and convert deployment to DeploymentConfig.

        Accepts:
        - DeploymentConfig instance
        - dict that can be converted to DeploymentConfig

        """
        if isinstance(v, DeploymentConfig):
            return v
        elif isinstance(v, dict):
            return DeploymentConfig(**v)  # type: ignore[arg-type]
        else:
            raise ValueError("deployment must be a dict, DeploymentConfig instance")

    def get_entrypoint(self, config_path: Path | None = None) -> EntrypointConfig:
        """Get the entrypoint as a structured object with resolved paths.

        Args:
            config_path: Path to config file for resolving relative paths

        Returns:
            EntrypointConfig object with file, object, and repo fields.
            If config_path is provided, relative file paths are resolved
            relative to the config file location.
        """
        if isinstance(self.entrypoint, str):
            # Parse string format into structured object
            if ":" in self.entrypoint:
                file, obj = self.entrypoint.rsplit(":", 1)
                entrypoint = EntrypointConfig(file=file, object=obj)
            else:
                entrypoint = EntrypointConfig(file=self.entrypoint)
        else:
            # Already an EntrypointConfig
            entrypoint = self.entrypoint

        # Resolve relative paths if config_path provided
        if config_path:
            file_path = Path(entrypoint.file)
            if not file_path.is_absolute():
                resolved_path = (config_path.parent / file_path).resolve()
                # Create new EntrypointConfig with resolved path
                entrypoint = EntrypointConfig(
                    file=str(resolved_path),
                    object=entrypoint.object,
                    repo=entrypoint.repo,
                )

        return entrypoint

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
        entrypoint: str,
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
            entrypoint: Server entrypoint (file or file:object)
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
            environment = EnvironmentConfig(
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
            deployment = DeploymentConfig(
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
            entrypoint=entrypoint,
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

    async def load_server(self, config_path: Path | None = None) -> Any:
        """Load the server from the configuration.

        This handles environment setup, working directory changes,
        and imports the server module.

        Args:
            config_path: Path to the config file (for resolving relative paths)

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

        # Get structured entrypoint with resolved paths
        entrypoint = self.get_entrypoint(config_path)

        # Import the server
        from fastmcp.cli.run import import_server_with_args

        file_path = Path(entrypoint.file)
        server_args = self.deployment.args if self.deployment else None

        return await import_server_with_args(file_path, entrypoint.object, server_args)

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


def generate_schema() -> dict[str, Any]:
    """Generate JSON schema for fastmcp.json files.

    This is used to create the schema file that IDEs can use for
    validation and auto-completion.

    Returns:
        JSON schema as a dictionary
    """
    schema = FastMCPConfig.model_json_schema()

    # Add some metadata
    schema["$id"] = FASTMCP_JSON_SCHEMA
    schema["title"] = "FastMCP Configuration"
    schema["description"] = "Configuration file for FastMCP servers"

    return schema
