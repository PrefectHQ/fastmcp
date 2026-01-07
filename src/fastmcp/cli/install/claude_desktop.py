"""Claude Desktop integration for FastMCP install using Cyclopts."""

import os
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich import print

from fastmcp.mcp_config import StdioMCPServer, update_config_file
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment

from .shared import process_common_args

logger = get_logger(__name__)


def get_claude_config_path() -> Path | None:
    """Get the Claude config directory based on platform."""
    if sys.platform == "win32":
        path = Path(Path.home(), "AppData", "Roaming", "Claude")
    elif sys.platform == "darwin":
        path = Path(Path.home(), "Library", "Application Support", "Claude")
    elif sys.platform.startswith("linux"):
        path = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"), "Claude"
        )
    else:
        return None

    if path.exists():
        return path
    return None


def install_claude_desktop(
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
    """Install FastMCP server in Claude Desktop.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Claude's config
        with_editable: Optional list of directories to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables
        python_version: Optional Python version to use
        with_requirements: Optional requirements file to install from
        project: Optional project directory to run within

    Returns:
        True if installation was successful, False otherwise
    """
    config_dir = get_claude_config_path()
    if not config_dir:
        print(
            "[red]Claude Desktop config directory not found.[/red]\n"
            "[blue]Please ensure Claude Desktop is installed and has been run at least once to initialize its config.[/blue]"
        )
        return False

    config_file = config_dir / "claude_desktop_config.json"

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

    # Create server configuration
    server_config = StdioMCPServer(
        command=full_command[0],
        args=full_command[1:],
        env=env_vars or {},
    )

    try:
        # Handle environment variable merging manually since we need to preserve existing config
        if config_file.exists():
            import json

            content = config_file.read_text().strip()
            if content:
                config = json.loads(content)
                if "mcpServers" in config and name in config["mcpServers"]:
                    existing_env = config["mcpServers"][name].get("env", {})
                    if env_vars:
                        # New vars take precedence over existing ones
                        merged_env = {**existing_env, **env_vars}
                    else:
                        merged_env = existing_env
                    server_config.env = merged_env

        # Update configuration with correct function signature
        update_config_file(config_file, name, server_config)
        print(f"[green]Successfully installed '{name}' in Claude Desktop[/green]")
        return True
    except Exception as e:
        print(f"[red]Failed to install server: {e}[/red]")
        return False


async def claude_desktop_command(
    server_spec: str,
    *,
    server_name: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--name", "-n"],
            help="Custom name for the server in Claude Desktop's config",
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
    """Install an MCP server in Claude Desktop.

    Args:
        server_spec: Python file to install, optionally with :object suffix
    """
    # Convert None to empty lists for list parameters
    with_editable = with_editable or []
    with_packages = with_packages or []
    env_vars = env_vars or []
    file, server_object, name, with_packages, env_dict = await process_common_args(
        server_spec, server_name, with_packages, env_vars, env_file
    )

    success = install_claude_desktop(
        file=file,
        server_object=server_object,
        name=name,
        with_editable=with_editable,
        with_packages=with_packages,
        env_vars=env_dict,
        python_version=python,
        with_requirements=with_requirements,
        project=project,
    )

    if not success:
        sys.exit(1)
