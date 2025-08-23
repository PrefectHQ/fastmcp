from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
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


def build_uv_command(
    server_spec: str,
    *,
    with_editable: Path | None = None,
    with_packages: list[str] | None = None,
    python_version: str | None = None,
    with_requirements: Path | None = None,
    project: Path | None = None,
) -> list[str]:
    """Build a uv run command for running a FastMCP server.

    This centralized function ensures consistent path resolution and command building
    across all CLI commands.

    Args:
        server_spec: Server specification (file path, optionally with :object)
        with_editable: Directory to install in editable mode
        with_packages: Additional packages to install
        python_version: Python version to use (e.g., "3.10", "3.11")
        with_requirements: Requirements file to install from
        project: Project directory to run within

    Returns:
        List of command arguments for subprocess execution
    """
    cmd = ["uv", "run"]

    # Add Python version if specified
    if python_version:
        cmd.extend(["--python", python_version])

    # Add project if specified - resolve to absolute path
    if project:
        cmd.extend(["--project", str(project.expanduser().resolve())])

    # Always include fastmcp
    cmd.extend(["--with", "fastmcp"])

    # Add additional packages
    if with_packages:
        # Deduplicate and sort packages for consistency
        packages = set(pkg for pkg in with_packages if pkg)
        for pkg in sorted(packages):
            cmd.extend(["--with", pkg])

    # Add editable directory - resolve to absolute path
    if with_editable:
        cmd.extend(["--with-editable", str(with_editable.expanduser().resolve())])

    # Add requirements file - resolve to absolute path
    if with_requirements:
        cmd.extend(
            ["--with-requirements", str(with_requirements.expanduser().resolve())]
        )

    # Add fastmcp run command
    cmd.extend(["fastmcp", "run", server_spec])

    return cmd


def build_uv_run_args(
    *,
    with_editable: Path | None = None,
    with_packages: list[str] | None = None,
    python_version: str | None = None,
    with_requirements: Path | None = None,
    project: Path | None = None,
) -> list[str]:
    """Build just the uv run arguments without the server spec.

    This is useful for install commands that need to build the args array
    without the full command structure.

    Args:
        with_editable: Directory to install in editable mode
        with_packages: Additional packages to install (fastmcp will be added automatically)
        python_version: Python version to use
        with_requirements: Requirements file to install from
        project: Project directory to run within

    Returns:
        List of arguments starting with "run"
    """
    args = ["run"]

    # Add Python version if specified
    if python_version:
        args.extend(["--python", python_version])

    # Add project if specified - resolve to absolute path
    if project:
        args.extend(["--project", str(project.expanduser().resolve())])

    # Collect all packages in a set to deduplicate
    packages = {"fastmcp"}
    if with_packages:
        packages.update(pkg for pkg in with_packages if pkg)

    # Add all packages with --with
    for pkg in sorted(packages):
        args.extend(["--with", pkg])

    # Add editable directory - resolve to absolute path
    if with_editable:
        args.extend(["--with-editable", str(with_editable.expanduser().resolve())])

    # Add requirements file - resolve to absolute path
    if with_requirements:
        args.extend(
            ["--with-requirements", str(with_requirements.expanduser().resolve())]
        )

    return args
