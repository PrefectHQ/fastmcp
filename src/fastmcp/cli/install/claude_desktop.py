"""Claude Desktop integration for FastMCP install."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from rich import print

from fastmcp.mcp_config import StdioMCPServer, update_config_file
from fastmcp.utilities.logging import get_logger

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
            "[red]❌ Claude Desktop config directory not found.[/red]\n"
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

        update_config_file(config_file, name, server_config)
        return True
    except Exception as e:
        print(
            f"[red]Failed to install '[bold]{name}[/bold]' in Claude Desktop: {e}[/red]"
        )
        return False
