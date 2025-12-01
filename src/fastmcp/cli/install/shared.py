"""Shared utilities for install commands."""

import json
import sys
from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError
from rich import print

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config import MCPServerConfig
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource

logger = get_logger(__name__)


def parse_env_var(env_var: str) -> tuple[str, str]:
    """Parse environment variable string in format KEY=VALUE."""
    if "=" not in env_var:
        print(
            f"[red]Invalid environment variable format: '[bold]{env_var}[/bold]'. Must be KEY=VALUE[/red]"
        )
        sys.exit(1)
    key, value = env_var.split("=", 1)
    return key.strip(), value.strip()


async def process_common_args(
    server_spec: str,
    server_name: str | None,
    with_packages: list[str] | None,
    env_vars: list[str] | None,
    env_file: Path | None,
) -> tuple[Path, str | None, str, list[str], dict[str, str] | None]:
    """Process common arguments shared by all install commands.

    Handles both fastmcp.json config files and traditional file.py:object syntax.
    """
    # Convert None to empty lists for list parameters
    with_packages = with_packages or []
    env_vars = env_vars or []
    # Create MCPServerConfig from server_spec
    config = None
    if server_spec.endswith(".json"):
        config_path = Path(server_spec).resolve()
        if not config_path.exists():
            print(f"[red]Configuration file not found: {config_path}[/red]")
            sys.exit(1)

        try:
            with open(config_path) as f:
                data = json.load(f)

            # Check if it's an MCPConfig (has mcpServers key)
            if "mcpServers" in data:
                # MCPConfig files aren't supported for install
                print("[red]MCPConfig files are not supported for installation[/red]")
                sys.exit(1)
            else:
                # It's a MCPServerConfig
                config = MCPServerConfig.from_file(config_path)

                # Merge packages from config if not overridden
                if config.environment.dependencies:
                    # Merge with CLI packages (CLI takes precedence)
                    config_packages = list(config.environment.dependencies)
                    with_packages = list(set(with_packages + config_packages))
        except (json.JSONDecodeError, ValidationError) as e:
            print(f"[red]Invalid configuration file: {e}[/red]")
            sys.exit(1)
    else:
        # Create config from file path
        source = FileSystemSource(path=server_spec)
        config = MCPServerConfig(source=source)

    # Extract file and server_object from the source
    # The FileSystemSource handles parsing path:object syntax
    file = Path(config.source.path).resolve()
    server_object = (
        config.source.entrypoint if hasattr(config.source, "entrypoint") else None
    )

    logger.debug(
        "Installing server",
        extra={
            "file": str(file),
            "server_name": server_name,
            "server_object": server_object,
            "with_packages": with_packages,
        },
    )

    # Try to import server to get its name and dependencies
    name = server_name
    server = None
    if not name:
        try:
            server = await config.source.load_server()
            name = server.name
        except (ImportError, ModuleNotFoundError) as e:
            logger.debug(
                "Could not import server (likely missing dependencies), using file name",
                extra={"error": str(e)},
            )
            name = file.stem

    # Process environment variables if provided
    env_dict: dict[str, str] | None = None
    if env_file or env_vars:
        env_dict = {}
        # Load from .env file if specified
        if env_file:
            try:
                env_dict |= {
                    k: v for k, v in dotenv_values(env_file).items() if v is not None
                }
            except Exception as e:
                print(f"[red]Failed to load .env file: {e}[/red]")
                sys.exit(1)

        # Add command line environment variables
        for env_var in env_vars:
            key, value = parse_env_var(env_var)
            env_dict[key] = value

    return file, server_object, name, with_packages, env_dict
