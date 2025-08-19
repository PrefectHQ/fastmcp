"""Shared utilities for install commands."""

import sys
from pathlib import Path

from dotenv import dotenv_values
from rich import print

from fastmcp.cli.run import import_server, parse_file_path
from fastmcp.utilities.logging import get_logger

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
    with_packages: list[str],
    env_vars: list[str],
    env_file: Path | None,
) -> tuple[Path, str | None, str, list[str], dict[str, str] | None]:
    """Process common arguments shared by all install commands.

    Handles both fastmcp.json config files and traditional file.py:object syntax.
    """
    # Check if server_spec is a fastmcp.json file
    if server_spec.endswith("fastmcp.json") or "fastmcp.json" in Path(server_spec).name:
        from fastmcp.utilities.fastmcp_config import FastMCPConfig

        config_path = Path(server_spec).resolve()
        if not config_path.exists():
            print(f"[red]Configuration file not found: {config_path}[/red]")
            sys.exit(1)

        # Load config and get entrypoint
        config = FastMCPConfig.from_file(config_path)
        entrypoint = config.get_entrypoint(config_path)

        # Convert to file and server_object
        file = Path(entrypoint.file)
        server_object = entrypoint.object

        # Merge packages from config if not overridden
        if config.environment and config.environment.dependencies:
            # Merge with CLI packages (CLI takes precedence)
            config_packages = config.environment.dependencies or []
            with_packages = list(set(with_packages + config_packages))
    else:
        # Parse traditional server spec
        file, server_object = parse_file_path(server_spec)

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
            server = await import_server(file, server_object)
            name = server.name
        except (ImportError, ModuleNotFoundError) as e:
            logger.debug(
                "Could not import server (likely missing dependencies), using file name",
                extra={"error": str(e)},
            )
            name = file.stem

    # Get server dependencies if available
    # TODO: Remove dependencies handling (deprecated in v2.11.4)
    server_dependencies = getattr(server, "dependencies", []) if server else []
    if server_dependencies:
        import warnings

        warnings.warn(
            "Server uses deprecated 'dependencies' parameter (deprecated in FastMCP 2.11.4). "
            "Please migrate to fastmcp.json configuration file. "
            "See https://gofastmcp.com/docs/deployment/server-configuration for details.",
            DeprecationWarning,
            stacklevel=2,
        )
        with_packages = list(set(with_packages + server_dependencies))

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
