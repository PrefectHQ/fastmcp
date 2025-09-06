"""Install subcommands for FastMCP CLI using Cyclopts."""

import cyclopts

from .claude_code import claude_code_command
from .claude_desktop import claude_desktop_command
from .cursor import cursor_command
from .gemini_cli import gemini_cli_command
from .mcp_json import mcp_json_command

# Create a cyclopts app for install subcommands
install_app = cyclopts.App(
    name="install",
    help="Install MCP servers in various clients and formats.",
)

# Register each command from its respective module
install_app.command(claude_code_command, name="claude-code")
install_app.command(claude_desktop_command, name="claude-desktop")
install_app.command(cursor_command, name="cursor")
install_app.command(gemini_cli_command, name="gemini-cli")
install_app.command(mcp_json_command, name="mcp-json")
