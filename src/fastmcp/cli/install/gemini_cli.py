"""Gemini CLI integration for FastMCP install using Cyclopts."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich import print

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment

from .shared import process_common_args

logger = get_logger(__name__)


def find_gemini_command() -> str | None:
    """Find the Gemini CLI command."""
    # First try shutil.which() in case it's a real executable in PATH
    gemini_in_path = shutil.which("gemini")
    if gemini_in_path:
        try:
            # If 'gemini --version' fails, it's not the correct path
            subprocess.run(
                [gemini_in_path, "--version"],
                check=True,
                capture_output=True,
            )
            return gemini_in_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    # Check common installation locations (aliases don't work with subprocess)
    potential_paths = [
        # Default Gemini CLI installation location (after migration)
        Path.home() / ".gemini" / "local" / "gemini",
        # npm global installation on macOS/Linux (default)
        Path("/usr/local/bin/gemini"),
        # npm global installation with custom prefix
        Path.home() / ".npm-global" / "bin" / "gemini",
        # Homebrew installation on macOS
        Path("/opt/homebrew/bin/gemini"),
    ]

    for path in potential_paths:
        if path.exists():
            # If 'gemini --version' fails, it's not the correct path
            try:
                subprocess.run(
                    [str(path), "--version"],
                    check=True,
                    capture_output=True,
                )
                return str(path)
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue

    return None


def check_gemini_cli_available() -> bool:
    """Check if Gemini CLI is available."""
    return find_gemini_command() is not None


def install_gemini_cli(
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
    """Install FastMCP server in Gemini CLI.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Gemini CLI
        with_editable: Optional list of directories to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables
        python_version: Optional Python version to use
        with_requirements: Optional requirements file to install from
        project: Optional project directory to run within

    Returns:
        True if installation was successful, False otherwise
    """
    # Check if Gemini CLI is available
    gemini_cmd = find_gemini_command()
    if not gemini_cmd:
        print(
            "[red]Gemini CLI not found.[/red]\n"
            "[blue]Please ensure Gemini CLI is installed. Try running 'gemini --version' to verify.[/blue]\n"
            "[blue]You can install it using 'npm install -g @google/gemini-cli'.[/blue]\n"
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

    # Build gemini mcp add command
    cmd_parts = [gemini_cmd, "mcp", "add"]

    # Add environment variables if specified (before the name and command)
    if env_vars:
        for key, value in env_vars.items():
            cmd_parts.extend(["-e", f"{key}={value}"])

    # Add server name and command
    cmd_parts.extend([name, full_command[0], "--"])
    cmd_parts.extend(full_command[1:])

    try:
        # Run the gemini mcp add command
        subprocess.run(cmd_parts, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(
            f"[red]Failed to install '[bold]{name}[/bold]' in Gemini CLI: {e.stderr.strip() if e.stderr else str(e)}[/red]"
        )
        return False
    except Exception as e:
        print(f"[red]Failed to install '[bold]{name}[/bold]' in Gemini CLI: {e}[/red]")
        return False


async def gemini_cli_command(
    server_spec: str,
    *,
    server_name: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--name", "-n"],
            help="Custom name for the server in Gemini CLI",
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
    """Install an MCP server in Gemini CLI.

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

    success = install_gemini_cli(
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

    if success:
        print(f"[green]Successfully installed '{name}' in Gemini CLI")
    else:
        sys.exit(1)
