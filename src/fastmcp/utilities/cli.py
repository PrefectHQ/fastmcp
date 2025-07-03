from __future__ import annotations

from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Literal

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import fastmcp

if TYPE_CHECKING:
    from fastmcp import FastMCP

LOGO_ASCII = r"""
    _ __ ___ ______           __  __  _____________       ____    ____ 
   _ __ ___ / ____/___ ______/ /_/  |/  / ____/ __ \     |___ \  / __ \
  _ __ ___ / /_  / __ `/ ___/ __/ /|_/ / /   / /_/ /     ___/ / / / / /
 _ __ ___ / __/ / /_/ (__  ) /_/ /  / / /___/ ____/     /  __/_/ /_/ / 
_ __ ___ /_/    \__,_/____/\__/_/  /_/\____/_/         /_____(_)____/  

""".lstrip("\n")


def create_server_banner(
    server: FastMCP[Any],
    transport: Literal["stdio", "http", "sse", "streamable-http"],
    *,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
) -> str:
    """Creates a formatted banner (as a string) with server information and logo.

    Args:
        transport: The transport protocol being used
        server_name: Optional server name to display
        host: Host address (for HTTP transports)
        port: Port number (for HTTP transports)
        path: Server path (for HTTP transports)

    Returns:
        A string representation of the banner.
    """

    # Create the logo text
    logo_text = Text(LOGO_ASCII, style="bold green")

    # Create the information table
    info_table = Table.grid(padding=(0, 1))
    info_table.add_column(style="bold cyan", justify="left")
    info_table.add_column(style="white", justify="left")

    match transport:
        case "http" | "streamable-http":
            display_transport = "Streamable-HTTP"
        case "sse":
            display_transport = "SSE"
        case "stdio":
            display_transport = "STDIO"

    info_table.add_row("Transport:", display_transport)

    # Show connection info based on transport
    if transport in ("http", "streamable-http", "sse"):
        if host and port:
            server_url = f"http://{host}:{port}"
            if path:
                server_url += f"/{path.lstrip('/')}"
            info_table.add_row("Server URL:", server_url)

    # Add documentation link
    info_table.add_row()
    info_table.add_row("Docs:", "https://gofastmcp.com")
    info_table.add_row("Hosting:", "https://fastmcp.cloud")

    # Add version information with explicit style overrides
    info_table.add_row()
    info_table.add_row(
        "FastMCP version:",
        Text(fastmcp.__version__, style="dim white", no_wrap=True),
    )
    info_table.add_row(
        "MCP version:",
        Text(version("mcp"), style="dim white", no_wrap=True),
    )
    # Create panel with logo and information using Group
    panel_content = Group(logo_text, "", info_table)

    # Use server name in title if provided
    title = "FastMCP 2.0"
    if server.name != "FastMCP":
        title += f" - {server.name}"

    panel = Panel(
        panel_content,
        title=title,
        title_align="left",
        border_style="dim",
        padding=(2, 5),
        expand=False,
    )

    console = Console()
    with console.capture() as capture:
        console.print(panel)
    rendered = capture.get()
    return f"\n\n{rendered}"
