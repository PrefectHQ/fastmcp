"""Codex CLI integration for FastMCP install using Cyclopts."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich import print
from rich.markup import escape

from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment

from .shared import process_common_args, run_cli_command, validate_server_name


def find_codex_command() -> str | None:
    """Find the Codex CLI command."""
    return shutil.which("codex")


def install_codex_cli(
    file: Path,
    server_object: str | None,
    name: str,
    *,
    with_editable: list[Path] | None = None,
    with_packages: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    python_version: str | None = None,
    with_requirements: Path | None = None,
    project: Path | None = None,
) -> bool:
    """Install FastMCP server in Codex CLI.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Codex CLI
        with_editable: Optional list of directories to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables
        python_version: Optional Python version to use
        with_requirements: Optional requirements file to install from
        project: Optional project directory to run within

    Returns:
        True if installation was successful, False otherwise
    """
    # Check if Codex CLI is available
    codex_cmd = find_codex_command()
    if not codex_cmd:
        print(
            "[red]Codex CLI not found.[/red]\n"
            "[blue]Please ensure Codex CLI is installed and on your PATH. Try running 'codex --version' to verify.[/blue]"
        )
        return False

    env_config = UVEnvironment(
        python=python_version,
        dependencies=(with_packages or []) + ["fastmcp"],
        requirements=with_requirements,
        project=project,
        editable=with_editable,
    )

    # Build server spec from parsed components
    if server_object:
        server_spec = f"{file.resolve()}:{server_object}"
    else:
        server_spec = str(file.resolve())

    # Build the full command
    full_command = env_config.build_command(["fastmcp", "run", server_spec])

    # Codex rejects spaces in server names
    name = validate_server_name(name).replace(" ", "-")

    # Build codex mcp add command
    cmd_parts = [codex_cmd, "mcp", "add", name]

    # Add environment variables if specified
    if env_vars:
        for key, value in env_vars.items():
            cmd_parts.extend(["--env", f"{key}={value}"])

    # Add server command
    cmd_parts.append("--")
    cmd_parts.extend(full_command)

    try:
        # Run the codex mcp add command
        run_cli_command(cmd_parts)
    except subprocess.CalledProcessError as e:
        # str(e) would print the environment variables
        reason = e.stderr.strip() or f"exit code {e.returncode}"
    except (OSError, ValueError) as e:
        reason = str(e)
    else:
        print(f"[green]Successfully installed '{name}' in Codex CLI[/green]")
        return True

    print(
        f"[red]Failed to install '[bold]{name}[/bold]' in Codex CLI: {escape(reason)}[/red]"
    )
    return False


async def codex_cli_command(
    server_spec: str,
    *,
    server_name: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--name", "-n"],
            help="Custom name for the server in Codex CLI",
        ),
    ] = None,
    with_editable: Annotated[
        list[Path] | None,
        cyclopts.Parameter(
            "--with-editable",
            help="Directory with pyproject.toml to install in editable mode (can be used multiple times)",
        ),
    ] = None,
    with_packages: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--with", help="Additional packages to install (can be used multiple times)"
        ),
    ] = None,
    env_vars: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--env",
            help="Environment variables in KEY=VALUE format (can be used multiple times)",
        ),
    ] = None,
    env_file: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--env-file",
            help="Load environment variables from .env file",
        ),
    ] = None,
    python: Annotated[
        str | None,
        cyclopts.Parameter(
            "--python",
            help="Python version to use (e.g., 3.12, 3.13)",
        ),
    ] = None,
    with_requirements: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--with-requirements",
            help="Requirements file to install dependencies from",
        ),
    ] = None,
    project: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--project",
            help="Run the command within the given project directory",
        ),
    ] = None,
) -> None:
    """Install an MCP server in Codex CLI.

    Args:
        server_spec: Python file to install, optionally with :object suffix
    """
    # Convert None to empty lists for list parameters
    with_editable = with_editable or []
    with_packages = with_packages or []
    env_vars = env_vars or []
    file, server_object, name, packages, env_dict = await process_common_args(
        server_spec, server_name, with_packages, env_vars, env_file
    )

    success = install_codex_cli(
        file=file,
        server_object=server_object,
        name=name,
        with_editable=with_editable,
        with_packages=packages,
        env_vars=env_dict,
        python_version=python,
        with_requirements=with_requirements,
        project=project,
    )

    if not success:
        sys.exit(1)
