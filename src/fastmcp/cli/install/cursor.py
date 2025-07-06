"""Cursor integration for FastMCP install."""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

from rich import print

from fastmcp.mcp_config import StdioMCPServer
from fastmcp.utilities.logging import get_logger

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
    server_spec: str,
    name: str,
    *,
    with_editable: Path | None = None,
    with_packages: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
) -> bool:
    """Install FastMCP server in Cursor.

    Args:
        server_spec: Path to the server file, optionally with :object suffix
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

    # Convert file path to absolute before adding to command
    # Split off any :object suffix first
    if ":" in server_spec:
        file_path, server_object = server_spec.rsplit(":", 1)
        server_spec = f"{Path(file_path).resolve()}:{server_object}"
    else:
        server_spec = str(Path(server_spec).resolve())

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
