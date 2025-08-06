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
    """Process common arguments shared by all install commands."""
    # Parse server spec
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
    server_dependencies = getattr(server, "dependencies", []) if server else []
    if server_dependencies:
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
