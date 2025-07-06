"""Claude Code integration for FastMCP install."""

from __future__ import annotations

import subprocess
from pathlib import Path

from rich import print

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


def find_claude_command() -> str | None:
    """Find the Claude Code CLI command."""
    # Check the default installation location
    default_path = Path.home() / ".claude" / "local" / "claude"
    if default_path.exists():
        try:
            result = subprocess.run(
                [str(default_path), "--version"],
                check=True,
                capture_output=True,
                text=True,
            )
            if "Claude Code" in result.stdout:
                return str(default_path)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    return None


def check_claude_code_available() -> bool:
    """Check if Claude Code CLI is available."""
    return find_claude_command() is not None


def install_claude_code(
    file: Path,
    server_object: str | None,
    name: str,
    *,
    with_editable: Path | None = None,
    with_packages: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
) -> bool:
    """Install FastMCP server in Claude Code.

    Args:
        file: Path to the server file
        server_object: Optional server object name (for :object suffix)
        name: Name for the server in Claude Code
        with_editable: Optional directory to install in editable mode
        with_packages: Optional list of additional packages to install
        env_vars: Optional dictionary of environment variables

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

    # Build claude mcp add command
    cmd_parts = [claude_cmd, "mcp", "add"]

    # Add environment variables if specified (before the name and command)
    if env_vars:
        for key, value in env_vars.items():
            cmd_parts.extend(["-e", f"{key}={value}"])

    # Add server name and command
    cmd_parts.extend([name, "--"])
    cmd_parts.extend(["uv"] + args)

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
