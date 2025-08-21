from __future__ import annotations

from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Literal

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import fastmcp

if TYPE_CHECKING:
    from fastmcp import FastMCP

LOGO_ASCII = r"""
    _ __ ___  _____           __  __  _____________    ____    ____ 
   _ __ ___ .'____/___ ______/ /_/  |/  / ____/ __ \  |___ \  / __ \
  _ __ ___ / /_  / __ `/ ___/ __/ /|_/ / /   / /_/ /  ___/ / / / / /
 _ __ ___ / __/ / /_/ (__  ) /_/ /  / / /___/ ____/  /  __/_/ /_/ / 
_ __ ___ /_/    \____/____/\__/_/  /_/\____/_/      /_____(*)____/  

""".lstrip("\n")


def log_server_banner(
    server: FastMCP[Any],
    transport: Literal["stdio", "http", "sse", "streamable-http"],
    *,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
) -> None:
    """Creates and logs a formatted banner with server information and logo.

    Args:
        transport: The transport protocol being used
        server_name: Optional server name to display
        host: Host address (for HTTP transports)
        port: Port number (for HTTP transports)
        path: Server path (for HTTP transports)
    """

    # Create the logo text
    logo_text = Text(LOGO_ASCII, style="bold green")

    # Create the main title
    title_text = Text("FastMCP  2.0", style="bold blue")

    # Create the information table
    info_table = Table.grid(padding=(0, 1))
    info_table.add_column(style="bold", justify="center")  # Emoji column
    info_table.add_column(style="cyan", justify="left")  # Label column
    info_table.add_column(style="dim", justify="left")  # Value column

    match transport:
        case "http" | "streamable-http":
            display_transport = "Streamable-HTTP"
        case "sse":
            display_transport = "SSE"
        case "stdio":
            display_transport = "STDIO"

    info_table.add_row("🖥️", "Server name:", server.name)
    info_table.add_row("📦", "Transport:", display_transport)

    # Show connection info based on transport
    if transport in ("http", "streamable-http", "sse"):
        if host and port:
            server_url = f"http://{host}:{port}"
            if path:
                server_url += f"/{path.lstrip('/')}"
            info_table.add_row("🔗", "Server URL:", server_url)

    # Add version information with explicit style overrides
    info_table.add_row("", "", "")
    info_table.add_row(
        "🏎️",
        "FastMCP version:",
        Text(fastmcp.__version__, style="dim white", no_wrap=True),
    )
    info_table.add_row(
        "🤝",
        "MCP SDK version:",
        Text(version("mcp"), style="dim white", no_wrap=True),
    )

    # Add documentation link
    info_table.add_row("", "", "")
    info_table.add_row("📚", "Docs:", "https://gofastmcp.com")
    info_table.add_row("🚀", "Deploy:", "https://fastmcp.cloud")

    # Create panel with logo, title, and information using Group
    panel_content = Group(
        Align.center(logo_text),
        Align.center(title_text),
        "",
        "",
        Align.center(info_table),
    )

    panel = Panel(
        panel_content,
        border_style="dim",
        padding=(1, 4),
        expand=False,
    )

    console = Console(stderr=True)
    console.print(Group("\n", panel, "\n"))
