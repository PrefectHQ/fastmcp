"""Claude Desktop integration for FastMCP install using Cyclopts."""

import os
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich import print

from fastmcp.mcp_config import StdioMCPServer, update_config_file
from fastmcp.utilities.logging import get_logger

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
    with_editable: Path | None = None,
    with_packages: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
) -> bool:
    """Install FastMCP server in Claude Desktop.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Claude's config
        with_editable: Optional directory to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables

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


def claude_desktop_command(
    server_spec: str,
    *,
    server_name: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--server-name", "-n"],
            help="Custom name for the server in Claude Desktop's config",
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
) -> None:
    """Install an MCP server in Claude Desktop.

    Args:
        server_spec: Python file to install, optionally with :object suffix
    """
    file, server_object, name, with_packages, env_dict = process_common_args(
        server_spec, server_name, with_packages, env_vars, env_file
    )

    success = install_claude_desktop(
        file=file,
        server_object=server_object,
        name=name,
        with_editable=with_editable,
        with_packages=with_packages,
        env_vars=env_dict,
    )

    if not success:
        sys.exit(1)
