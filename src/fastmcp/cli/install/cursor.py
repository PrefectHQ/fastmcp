"""Cursor integration for FastMCP install using Cyclopts."""

import base64
import subprocess
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


def install_cursor_workspace(
    file: Path,
    server_object: str | None,
    name: str,
    workspace_path: Path,
    *,
    with_editable: list[Path] | None = None,
    with_packages: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    python_version: str | None = None,
    with_requirements: Path | None = None,
    project: Path | None = None,
) -> bool:
    """Install FastMCP server to workspace-specific Cursor configuration.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Cursor
        workspace_path: Path to the workspace directory
        with_editable: Optional list of directories to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables
        python_version: Optional Python version to use
        with_requirements: Optional requirements file to install from
        project: Optional project directory to run within

    Returns:
        True if installation was successful, False otherwise
    """
    # Ensure workspace path is absolute and exists
    workspace_path = workspace_path.resolve()
    if not workspace_path.exists():
        print(f"[red]Workspace directory does not exist: {workspace_path}[/red]")
        return False

    # Create .cursor directory in workspace
    cursor_dir = workspace_path / ".cursor"
    cursor_dir.mkdir(exist_ok=True)

    config_file = cursor_dir / "mcp.json"

    env_config = UVEnvironment(
        python=python_version,
        dependencies=(with_packages or []) + ["fastmcp"],
        requirements=str(with_requirements.resolve()) if with_requirements else None,
        project=str(project.resolve()) if project else None,
        editable=[str(p.resolve()) for p in with_editable] if with_editable else None,
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
        # Create the config file if it doesn't exist
        if not config_file.exists():
            config_file.write_text('{"mcpServers": {}}')

        # Update configuration with the new server
        update_config_file(config_file, name, server_config)
        print(
            f"[green]Successfully installed '{name}' to workspace at {workspace_path}[/green]"
        )
        return True
    except Exception as e:
        print(f"[red]Failed to install server to workspace: {e}[/red]")
        return False


def install_cursor(
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
    workspace: Path | None = None,
) -> bool:
    """Install FastMCP server in Cursor.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Cursor
        with_editable: Optional list of directories to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables
        python_version: Optional Python version to use
        with_requirements: Optional requirements file to install from
        project: Optional project directory to run within
        workspace: Optional workspace directory for project-specific installation

    Returns:
        True if installation was successful, False otherwise
    """

    env_config = UVEnvironment(
        python=python_version,
        dependencies=(with_packages or []) + ["fastmcp"],
        requirements=str(with_requirements.resolve()) if with_requirements else None,
        project=str(project.resolve()) if project else None,
        editable=[str(p.resolve()) for p in with_editable] if with_editable else None,
    )
    # Build server spec from parsed components
    if server_object:
        server_spec = f"{file.resolve()}:{server_object}"
    else:
        server_spec = str(file.resolve())

    # Build the full command
    full_command = env_config.build_command(["fastmcp", "run", server_spec])

    # If workspace is specified, install to workspace-specific config
    if workspace:
        return install_cursor_workspace(
            file=file,
            server_object=server_object,
            name=name,
            workspace_path=workspace,
            with_editable=with_editable,
            with_packages=with_packages,
            env_vars=env_vars,
            python_version=python_version,
            with_requirements=with_requirements,
            project=project,
        )

    # Create server configuration
    server_config = StdioMCPServer(
        command=full_command[0],
        args=full_command[1:],
        env=env_vars or {},
    )

    # Generate deeplink
    deeplink = generate_cursor_deeplink(name, server_config)

    print(f"[blue]Opening Cursor to install '{name}'[/blue]")

    if open_deeplink(deeplink):
        print("[green]Cursor should now open with the installation dialog[/green]")
        return True
    else:
        print(
            "[red]Could not open Cursor automatically.[/red]\n"
            f"[blue]Please copy this link and open it in Cursor: {deeplink}[/blue]"
        )
        return False


async def cursor_command(
    server_spec: str,
    *,
    server_name: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--name", "-n"],
            help="Custom name for the server in Cursor",
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
    workspace: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--workspace",
            help="Install to workspace directory (will create .cursor/ inside it) instead of using deeplink",
        ),
    ] = None,
) -> None:
    """Install an MCP server in Cursor.

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

    success = install_cursor(
        file=file,
        server_object=server_object,
        name=name,
        with_editable=with_editable,
        with_packages=with_packages,
        env_vars=env_dict,
        python_version=python,
        with_requirements=with_requirements,
        project=project,
        workspace=workspace,
    )

    if not success:
        sys.exit(1)
