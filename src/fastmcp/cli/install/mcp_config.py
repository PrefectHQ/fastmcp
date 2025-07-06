"""MCP configuration JSON generation for FastMCP install."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich import print

from fastmcp.utilities.logging import get_logger

from .shared import process_common_args

logger = get_logger(__name__)


def install_mcp_config(
    file: Path,
    server_object: str | None,
    name: str,
    *,
    with_editable: Path | None = None,
    with_packages: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    copy: bool = False,
) -> bool:
    """Generate MCP configuration JSON for manual installation.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in MCP config
        with_editable: Optional directory to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables
        copy: If True, copy to clipboard instead of printing to stdout

    Returns:
        True if generation was successful, False otherwise
    """
    try:
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

        # Build MCP server configuration (just the server object, not the wrapper)
        config = {
            "command": "uv",
            "args": args,
        }

        # Add environment variables if provided
        if env_vars:
            config["env"] = env_vars

        # Convert to JSON
        json_output = json.dumps(config, indent=2)

        # Handle output
        if copy:
            try:
                import pyperclip

                pyperclip.copy(json_output)
                print(
                    f"[green]MCP configuration for '[bold]{name}[/bold]' copied to clipboard[/green]"
                )
            except ImportError:
                print(
                    "[red]The `--copy` flag requires pyperclip. Please install pyperclip and try again: `pip install pyperclip`[/red]"
                )
                return False
        else:
            # Print to stdout (for piping)
            print(json_output)

        return True

    except Exception as e:
        print(f"[red]❌ Failed to generate MCP configuration: {e}[/red]")
        return False


def mcp_config_command(
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
    copy: Annotated[
        bool,
        typer.Option(
            "--copy",
            help="Copy configuration to clipboard instead of printing to stdout",
        ),
    ] = False,
) -> None:
    """Generate MCP configuration JSON for manual installation."""
    file, server_object, name, packages, env_dict = process_common_args(
        server_spec, server_name, with_packages, env_vars, env_file
    )

    success = install_mcp_config(
        file=file,
        server_object=server_object,
        name=name,
        with_editable=with_editable,
        with_packages=packages,
        env_vars=env_dict,
        copy=copy,
    )

    # mcp-config handles its own messaging, no generic success message needed
    if not success:
        sys.exit(1)
