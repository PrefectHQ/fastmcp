"""Cursor integration for FastMCP install."""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich import print

from fastmcp.mcp_config import StdioMCPServer
from fastmcp.utilities.logging import get_logger

from .shared import process_common_args

logger = get_logger(__name__)


def generate_cursor_deeplink(
    server_name: str,
    server_config: StdioMCPServer,
) -> str:
    """Generate a Cursor deeplink for installing the MCP server.

    Args:
        server_name: Name of the server
        server_config: Server configuration

    Returns:
        Deeplink URL that can be clicked to install the server
    """
    # Create the configuration structure expected by Cursor

    # Base64 encode the configuration (URL-safe for query parameter)
    config_json = server_config.model_dump_json(exclude_none=True)
    config_b64 = base64.urlsafe_b64encode(config_json.encode()).decode()

    # Generate the deeplink URL
    deeplink = f"cursor://anysphere.cursor-deeplink/mcp/install?name={server_name}&config={config_b64}"

    return deeplink


def open_deeplink(deeplink: str) -> bool:
    """Attempt to open a deeplink URL using the system's default handler.

    Args:
        deeplink: The deeplink URL to open

    Returns:
        True if the command succeeded, False otherwise
    """
    try:
        if sys.platform == "darwin":  # macOS
            subprocess.run(["open", deeplink], check=True, capture_output=True)
        elif sys.platform == "win32":  # Windows
            subprocess.run(
                ["start", deeplink], shell=True, check=True, capture_output=True
            )
        else:  # Linux and others
            subprocess.run(["xdg-open", deeplink], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def install_cursor(
    file: Path,
    server_object: str | None,
    name: str,
    *,
    with_editable: Path | None = None,
    with_packages: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
) -> bool:
    """Install FastMCP server in Cursor.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Cursor's config
        with_editable: Optional directory to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables

    Returns:
        True if installation was successful, False otherwise
    """
    # Build uv run command
    args = ["run"]

    # Collect all packages in a set to deduplicate
    packages = {"fastmcp"}
    if with_packages:
        packages.update(pkg for pkg in with_packages if pkg)

    # Add all packages with --with
    for pkg in sorted(packages):
        args.extend(["--with", pkg])

    if with_editable:
        args.extend(["--with-editable", str(with_editable)])

    # Build server spec from parsed components
    if server_object:
        server_spec = f"{file.resolve()}:{server_object}"
    else:
        server_spec = str(file.resolve())

    # Add fastmcp run command
    args.extend(["fastmcp", "run", server_spec])

    # Create server configuration
    server_config = StdioMCPServer(
        command="uv",
        args=args,
        env=env_vars or {},
    )

    # Generate and open deeplink
    try:
        deeplink = generate_cursor_deeplink(name, server_config)

        if open_deeplink(deeplink):
            print(
                f"[green]Opening Cursor to install '[bold]{name}[/bold]' - please confirm in Cursor to complete installation[/green]"
            )
            return True
        else:
            print("[yellow]Could not open Cursor automatically.[/yellow]")
            print(f"[blue]Please open this link to install:[/blue] {deeplink}")
            return True

    except Exception as e:
        print(f"[red]Failed to generate Cursor deeplink: {e}[/red]")
        return False


def cursor_command(
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
            help="Directory containing pyproject.toml to install in editable mode",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ] = None,
    with_packages: Annotated[
        list[str],
        typer.Option(
            "--with", help="Additional packages to install, in PEP 508 format"
        ),
    ] = [],
    env_vars: Annotated[
        list[str],
        typer.Option(
            "--env-var", "-v", help="Environment variables in KEY=VALUE format"
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
    """Install a MCP server in Cursor."""
    file, server_object, name, packages, env_dict = process_common_args(
        server_spec, server_name, with_packages, env_vars, env_file
    )

    success = install_cursor(
        file=file,
        server_object=server_object,
        name=name,
        with_editable=with_editable,
        with_packages=packages,
        env_vars=env_dict,
    )

    # Cursor handles its own messaging, no generic success message needed
    if not success:
        sys.exit(1)
