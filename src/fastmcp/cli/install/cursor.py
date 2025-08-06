"""Cursor integration for FastMCP install using Cyclopts."""

import base64
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
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
    python_version: str | None = None,
    with_requirements: Path | None = None,
    project: Path | None = None,
) -> bool:
    """Install FastMCP server in Cursor.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Cursor
        with_editable: Optional directory to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables
        python_version: Optional Python version to use
        with_requirements: Optional requirements file to install from
        project: Optional project directory to run within

    Returns:
        True if installation was successful, False otherwise
    """
    # Build uv run command
    args = ["run"]

    # Add Python version if specified
    if python_version:
        args.extend(["--python", python_version])

    # Add project if specified
    if project:
        args.extend(["--project", str(project)])

    # Collect all packages in a set to deduplicate
    packages = {"fastmcp"}
    if with_packages:
        packages.update(pkg for pkg in with_packages if pkg)

    # Add all packages with --with
    for pkg in sorted(packages):
        args.extend(["--with", pkg])

    if with_editable:
        args.extend(["--with-editable", str(with_editable)])

    if with_requirements:
        args.extend(["--with-requirements", str(with_requirements)])

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
        Path | None,
        cyclopts.Parameter(
            name=["--with-editable", "-e"],
            help="Directory with pyproject.toml to install in editable mode",
        ),
    ] = None,
    with_packages: Annotated[
        list[str],
        cyclopts.Parameter(
            "--with",
            help="Additional packages to install",
            negative=False,
        ),
    ] = [],
    env_vars: Annotated[
        list[str],
        cyclopts.Parameter(
            "--env",
            help="Environment variables in KEY=VALUE format",
            negative=False,
        ),
    ] = [],
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
    """Install an MCP server in Cursor.

    Args:
        server_spec: Python file to install, optionally with :object suffix
    """
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
    )

    if not success:
        sys.exit(1)
