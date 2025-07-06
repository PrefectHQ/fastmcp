"""Install subcommands for FastMCP CLI."""

import typer

from .claude_code import claude_code_command
from .claude_desktop import claude_desktop_command
from .cursor import cursor_command
from .mcp_config import mcp_config_command

# Create a typer app for install subcommands
install_app = typer.Typer(
    name="install",
    help="Install MCP servers in various clients and formats",
    no_args_is_help=True,
)

# Register each command from its respective module
install_app.command("claude-code", help="Install a MCP server in Claude Code")(
    claude_code_command
)
install_app.command("claude-desktop", help="Install a MCP server in Claude Desktop")(
    claude_desktop_command
)
install_app.command("cursor", help="Install a MCP server in Cursor")(cursor_command)
install_app.command(
    "mcp-json", help="Generate MCP JSON configuration for manual installation"
)(mcp_config_command)
