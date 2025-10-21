"""Claude Code integration for FastMCP install using Cyclopts."""

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


def find_claude_command() -> str | None:
    """Find the Claude Code CLI command.

    Checks common installation locations since 'claude' is often a shell alias
    that doesn't work with subprocess calls.
    """
    # First try shutil.which() in case it's a real executable in PATH
    claude_in_path = shutil.which("claude")
    if claude_in_path:
        try:
            result = subprocess.run(
                [claude_in_path, "--version"],
                check=True,
                capture_output=True,
                text=True,
            )
            if "Claude Code" in result.stdout:
                return claude_in_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    # Check common installation locations (aliases don't work with subprocess)
    potential_paths = [
        # Default Claude Code installation location (after migration)
        Path.home() / ".claude" / "local" / "claude",
        # npm global installation on macOS/Linux (default)
        Path("/usr/local/bin/claude"),
        # npm global installation with custom prefix
        Path.home() / ".npm-global" / "bin" / "claude",
    ]

    for path in potential_paths:
        if path.exists():
            try:
                result = subprocess.run(
                    [str(path), "--version"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if "Claude Code" in result.stdout:
                    return str(path)
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue

    return None


def check_claude_code_available() -> bool:
    """Check if Claude Code CLI is available."""
    return find_claude_command() is not None


def install_claude_code(
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
    """Install FastMCP server in Claude Code.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Claude Code
        with_editable: Optional list of directories to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables
        python_version: Optional Python version to use
        with_requirements: Optional requirements file to install from
        project: Optional project directory to run within

    Returns:
        True if installation was successful, False otherwise
    """
    # Check if Claude Code CLI is available
    claude_cmd = find_claude_command()
    if not claude_cmd:
        print(
            "[red]Claude Code CLI not found.[/red]\n"
            "[blue]Please ensure Claude Code is installed. Try running 'claude --version' to verify.[/blue]"
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

    # Build claude mcp add command
    cmd_parts = [claude_cmd, "mcp", "add", name]

    # Add environment variables if specified
    if env_vars:
        for key, value in env_vars.items():
            cmd_parts.extend(["-e", f"{key}={value}"])

    # Add server name and command
    cmd_parts.append("--")
    cmd_parts.extend(full_command)

    try:
        # Run the claude mcp add command
        subprocess.run(cmd_parts, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(
            f"[red]Failed to install '[bold]{name}[/bold]' in Claude Code: {e.stderr.strip() if e.stderr else str(e)}[/red]"
        )
        return False
    except Exception as e:
        print(f"[red]Failed to install '[bold]{name}[/bold]' in Claude Code: {e}[/red]")
        return False


async def claude_code_command(
    server_spec: str,
    *,
    server_name: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--name", "-n"],
            help="Custom name for the server in Claude Code",
        ),
    ] = None,
    with_editable: Annotated[
        list[Path] | None,
        cyclopts.Parameter(
            "--with-editable",
            help="Directory with pyproject.toml to install in editable mode (can be used multiple times)",
            negative="",
        ),
    ] = None,
    with_packages: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--with",
            help="Additional packages to install (can be used multiple times)",
            negative="",
        ),
    ] = None,
    env_vars: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--env",
            help="Environment variables in KEY=VALUE format (can be used multiple times)",
            negative="",
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
    """Install an MCP server in Claude Code.

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

    success = install_claude_code(
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
        print(f"[green]Successfully installed '{name}' in Claude Code[/green]")
    else:
        sys.exit(1)
