"""Stdio command generation for FastMCP install using Cyclopts."""

import builtins
import shlex
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
import pyperclip
from rich import print as rich_print

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment

from .shared import process_common_args

logger = get_logger(__name__)


def install_stdio(
    file: Path,
    server_object: str | None,
    *,
    with_editable: list[Path] | None = None,
    with_packages: list[str] | None = None,
    copy: bool = False,
    python_version: str | None = None,
    with_requirements: Path | None = None,
    project: Path | None = None,
) -> bool:
    """Generate the stdio command for running a FastMCP server.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        with_editable: Optional list of directories to install in editable mode
        with_packages: Optional list of additional packages to install
        copy: If True, copy to clipboard instead of printing to stdout
        python_version: Optional Python version to use
        with_requirements: Optional requirements file to install from
        project: Optional project directory to run within

    Returns:
        True if generation was successful, False otherwise
    """
    try:
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
        command_str = shlex.join(full_command)

        if copy:
            pyperclip.copy(command_str)
            rich_print("[green]✓ Command copied to clipboard[/green]")
        else:
            builtins.print(command_str)

        return True

    except (OSError, ValueError, pyperclip.PyperclipException) as e:
        rich_print(f"[red]Failed to generate stdio command: {e}[/red]")
        return False


async def stdio_command(
    server_spec: str,
    *,
    server_name: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--name", "-n"],
            help="Custom name for the server (used for dependency resolution)",
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
    copy: Annotated[
        bool,
        cyclopts.Parameter(
            "--copy",
            help="Copy command to clipboard instead of printing to stdout",
        ),
    ] = False,
    python: Annotated[
        str | None,
        cyclopts.Parameter(
            "--python",
            help="Python version to use (e.g., 3.10, 3.11)",
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
    """Generate the stdio command for running a FastMCP server.

    Outputs the shell command that an MCP host would use to start this server
    over stdio transport. Useful for manual configuration or debugging.

    Args:
        server_spec: Python file to run, optionally with :object suffix
    """
    with_editable = with_editable or []
    with_packages = with_packages or []
    file, server_object, _name, packages, _env_dict = await process_common_args(
        server_spec, server_name, with_packages, [], None
    )

    success = install_stdio(
        file=file,
        server_object=server_object,
        with_editable=with_editable,
        with_packages=packages,
        copy=copy,
        python_version=python,
        with_requirements=with_requirements,
        project=project,
    )

    if not success:
        sys.exit(1)
