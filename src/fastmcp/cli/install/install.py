"""Main install logic for FastMCP CLI."""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from dotenv import dotenv_values
from rich import print

from fastmcp.cli.run import import_server, parse_file_path
from fastmcp.utilities.logging import get_logger

from .claude_code import install_claude_code
from .claude_desktop import install_claude_desktop
from .cursor import install_cursor

logger = get_logger(__name__)


class Client(str, Enum):
    """Supported MCP clients."""

    CLAUDE_CODE = "claude-code"
    CLAUDE_DESKTOP = "claude-desktop"
    CURSOR = "cursor"


def install(
    client: Annotated[
        Client,
        typer.Argument(help="MCP client to install the server into"),
    ],
    server_spec: Annotated[
        str, typer.Argument(help="Python file to run, optionally with :object suffix")
    ],
    server_name: Annotated[
        str | None,
        typer.Option(
            "--name",
            "-n",
            help="Custom name for the server (defaults to server's name attribute or file name)",
        ),
    ] = None,
    with_editable: Annotated[
        Path | None,
        typer.Option(
            "--with-editable",
            "-e",
            help="Directory containing pyproject.toml to install in editable mode. Use this to include local packages that are not available on PyPI.",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ] = None,
    with_packages: Annotated[
        list[str],
        typer.Option(
            "--with",
            help="Additional packages to install, in PEP 508 format (e.g. 'httpx>=0.25.2')",
        ),
    ] = [],
    env_vars: Annotated[
        list[str],
        typer.Option(
            "--env-var",
            "-v",
            help="Environment variables in KEY=VALUE format",
        ),
    ] = [],
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            "-f",
            help="Load environment variables from a .env file",
            exists=True,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Install a MCP server in the specified target application.

    Environment variables are preserved once added and only updated if new values
    are explicitly provided.
    """

    # Parse server spec
    file, server_object = parse_file_path(server_spec)

    logger.debug(
        "Installing server",
        extra={
            "client": client,
            "file": str(file),
            "server_name": server_name,
            "server_object": server_object,
            "with_editable": str(with_editable) if with_editable else None,
            "with_packages": with_packages,
        },
    )

    # Try to import server to get its name and dependencies
    name = server_name
    server = None
    if not name:
        try:
            server = import_server(file, server_object)
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
                print(f"[red]❌ Failed to load .env file: {e}[/red]")
                sys.exit(1)

        # Add command line environment variables
        for env_var in env_vars:
            key, value = _parse_env_var(env_var)
            env_dict[key] = value

    # Route to appropriate installer
    if client == Client.CLAUDE_CODE:
        success = install_claude_code(
            file=file,
            server_object=server_object,
            name=name,
            with_editable=with_editable,
            with_packages=with_packages,
            env_vars=env_dict,
        )
    elif client == Client.CLAUDE_DESKTOP:
        success = install_claude_desktop(
            file=file,
            server_object=server_object,
            name=name,
            with_editable=with_editable,
            with_packages=with_packages,
            env_vars=env_dict,
        )
    elif client == Client.CURSOR:
        success = install_cursor(
            file=file,
            server_object=server_object,
            name=name,
            with_editable=with_editable,
            with_packages=with_packages,
            env_vars=env_dict,
        )
    else:
        print(
            f"[red bold]Unknown client: {client!r}[/red bold]. Supported clients: [bold]{Client.CLAUDE_CODE}[/bold], [bold]{Client.CLAUDE_DESKTOP}[/bold], [bold]{Client.CURSOR}[/bold]"
        )
        raise typer.Exit(1)

    if success:
        # Only show generic success message for clients that don't have their own messaging
        if client != Client.CURSOR:
            print(
                f"[green bold]Successfully installed '[bold]{name}[/bold]' in {client.value}[/green bold]"
            )
    else:
        sys.exit(1)


def _parse_env_var(env_var: str) -> tuple[str, str]:
    """Parse environment variable string in format KEY=VALUE."""
    if "=" not in env_var:
        print(
            f"[red]❌ Invalid environment variable format: '[bold]{env_var}[/bold]'. Must be KEY=VALUE[/red]"
        )
        sys.exit(1)
    key, value = env_var.split("=", 1)
    return key.strip(), value.strip()
