from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import fastmcp
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config import MCPServerConfig
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource
from fastmcp.utilities.types import get_cached_typeadapter
from fastmcp.utilities.version_check import check_for_newer_version

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger("cli.config")


def is_already_in_uv_subprocess() -> bool:
    """Check if we're already running in a FastMCP uv subprocess."""
    return bool(os.environ.get("FASTMCP_UV_SPAWNED"))


def load_and_merge_config(
    server_spec: str | None,
    **cli_overrides,
) -> tuple[MCPServerConfig, str]:
    """Load config from server_spec and apply CLI overrides.

    This consolidates the config parsing logic that was duplicated across
    run, inspect, and dev commands.

    Args:
        server_spec: Python file, config file, URL, or None to auto-detect
        cli_overrides: CLI arguments that override config values

    Returns:
        Tuple of (MCPServerConfig, resolved_server_spec)
    """
    config = None
    config_path = None

    # Auto-detect fastmcp.json if no server_spec provided
    if server_spec is None:
        config_path = Path("fastmcp.json")
        if not config_path.exists():
            found_config = MCPServerConfig.find_config()
            if found_config:
                config_path = found_config
            else:
                logger.error(
                    "No server specification provided and no fastmcp.json found in current directory.\n"
                    "Please specify a server file or create a fastmcp.json configuration."
                )
                raise FileNotFoundError("No server specification or fastmcp.json found")

        resolved_spec = str(config_path)
        logger.info(f"Using configuration from {config_path}")
    else:
        resolved_spec = server_spec

    # Load config if server_spec is a .json file
    if resolved_spec.endswith(".json"):
        config_path = Path(resolved_spec)
        if config_path.exists():
            try:
                with open(config_path) as f:
                    data = json.load(f)

                # Check if it's an MCPConfig first (has canonical mcpServers key)
                if "mcpServers" in data:
                    # MCPConfig - we don't process these here, just pass through
                    pass
                else:
                    # Try to parse as MCPServerConfig
                    try:
                        adapter = get_cached_typeadapter(MCPServerConfig)
                        config = adapter.validate_python(data)

                        # Apply deployment settings
                        if config.deployment:
                            config.deployment.apply_runtime_settings(config_path)

                    except ValidationError:
                        # Not a valid MCPServerConfig, just pass through
                        pass
            except (json.JSONDecodeError, FileNotFoundError):
                # Not a valid JSON file, just pass through
                pass

    # If we don't have a config object yet, create one from filesystem source
    if config is None:
        source = FileSystemSource(path=resolved_spec)
        config = MCPServerConfig(source=source)

    # Convert to dict for immutable transformation
    config_dict = config.model_dump()

    # Apply CLI overrides to config's environment (always exists due to default_factory)
    if python_override := cli_overrides.get("python"):
        config_dict["environment"]["python"] = python_override
    if packages_override := cli_overrides.get("with_packages"):
        # Merge packages - CLI packages are added to config packages
        existing = config_dict["environment"].get("dependencies") or []
        config_dict["environment"]["dependencies"] = packages_override + existing
    if requirements_override := cli_overrides.get("with_requirements"):
        config_dict["environment"]["requirements"] = str(requirements_override)
    if project_override := cli_overrides.get("project"):
        config_dict["environment"]["project"] = str(project_override)
    if editable_override := cli_overrides.get("editable"):
        config_dict["environment"]["editable"] = editable_override

    # Apply deployment CLI overrides (always exists due to default_factory)
    if transport_override := cli_overrides.get("transport"):
        config_dict["deployment"]["transport"] = transport_override
    if host_override := cli_overrides.get("host"):
        config_dict["deployment"]["host"] = host_override
    if port_override := cli_overrides.get("port"):
        config_dict["deployment"]["port"] = port_override
    if path_override := cli_overrides.get("path"):
        config_dict["deployment"]["path"] = path_override
    if log_level_override := cli_overrides.get("log_level"):
        config_dict["deployment"]["log_level"] = log_level_override
    if server_args_override := cli_overrides.get("server_args"):
        config_dict["deployment"]["args"] = server_args_override

    # Create new config from modified dict
    new_config = MCPServerConfig(**config_dict)
    return new_config, resolved_spec


LOGO_ASCII_1 = r"""
    _ __ ___  _____           __  __  _____________    ____    ____ 
   _ __ ___ .'____/___ ______/ /_/  |/  / ____/ __ \  |___ \  / __ \
  _ __ ___ / /_  / __ `/ ___/ __/ /|_/ / /   / /_/ /  ___/ / / / / /
 _ __ ___ / __/ / /_/ (__  ) /_/ /  / / /___/ ____/  /  __/_/ /_/ / 
_ __ ___ /_/    \____/____/\__/_/  /_/\____/_/      /_____(*)____/  

""".lstrip("\n")

# This prints the below in a blue gradient
#  █▀▀ ▄▀█ █▀▀ ▀█▀ █▀▄▀█ █▀▀ █▀█
#  █▀  █▀█ ▄▄█  █  █ ▀ █ █▄▄ █▀▀
LOGO_ASCII_2 = (
    "\x1b[38;2;0;198;255m \x1b[38;2;0;195;255m█\x1b[38;2;0;192;255m▀\x1b[38;2;0;189;255m▀\x1b[38;2;0;186;255m "
    "\x1b[38;2;0;184;255m▄\x1b[38;2;0;181;255m▀\x1b[38;2;0;178;255m█\x1b[38;2;0;175;255m "
    "\x1b[38;2;0;172;255m█\x1b[38;2;0;169;255m▀\x1b[38;2;0;166;255m▀\x1b[38;2;0;163;255m "
    "\x1b[38;2;0;160;255m▀\x1b[38;2;0;157;255m█\x1b[38;2;0;155;255m▀\x1b[38;2;0;152;255m "
    "\x1b[38;2;0;149;255m█\x1b[38;2;0;146;255m▀\x1b[38;2;0;143;255m▄\x1b[38;2;0;140;255m▀\x1b[38;2;0;137;255m█\x1b[38;2;0;134;255m "
    "\x1b[38;2;0;131;255m█\x1b[38;2;0;128;255m▀\x1b[38;2;0;126;255m▀\x1b[38;2;0;123;255m "
    "\x1b[38;2;0;120;255m█\x1b[38;2;0;117;255m▀\x1b[38;2;0;114;255m█\x1b[39m\n"
    "\x1b[38;2;0;198;255m \x1b[38;2;0;195;255m█\x1b[38;2;0;192;255m▀\x1b[38;2;0;189;255m \x1b[38;2;0;186;255m "
    "\x1b[38;2;0;184;255m█\x1b[38;2;0;181;255m▀\x1b[38;2;0;178;255m█\x1b[38;2;0;175;255m "
    "\x1b[38;2;0;172;255m▄\x1b[38;2;0;169;255m▄\x1b[38;2;0;166;255m█\x1b[38;2;0;163;255m "
    "\x1b[38;2;0;160;255m \x1b[38;2;0;157;255m█\x1b[38;2;0;155;255m \x1b[38;2;0;152;255m "
    "\x1b[38;2;0;149;255m█\x1b[38;2;0;146;255m \x1b[38;2;0;143;255m▀\x1b[38;2;0;140;255m \x1b[38;2;0;137;255m█\x1b[38;2;0;134;255m "
    "\x1b[38;2;0;131;255m█\x1b[38;2;0;128;255m▄\x1b[38;2;0;126;255m▄\x1b[38;2;0;123;255m "
    "\x1b[38;2;0;120;255m█\x1b[38;2;0;117;255m▀\x1b[38;2;0;114;255m▀\x1b[39m"
).strip()

# Prints the below in a blue gradient - sylized F
#  ▄▀▀▀
#  █▀▀
# ▀
LOGO_ASCII_3 = (
    " \x1b[38;2;0;170;255m▄\x1b[38;2;0;142;255m▀\x1b[38;2;0;114;255m▀\x1b[38;2;0;86;255m▀\x1b[39m\n"
    " \x1b[38;2;0;170;255m█\x1b[38;2;0;142;255m▀\x1b[38;2;0;114;255m▀\x1b[39m\n"
    "\x1b[38;2;0;170;255m▀\x1b[39m\n"
    "\x1b[0m"
)

# Prints the below in a blue gradient - block logo with slightly stylized F
#  ▄▀▀ ▄▀█ █▀▀ ▀█▀ █▀▄▀█ █▀▀ █▀█
#  █▀  █▀█ ▄▄█  █  █ ▀ █ █▄▄ █▀▀

LOGO_ASCII_4 = (
    "\x1b[38;2;0;198;255m \x1b[38;2;0;195;255m▄\x1b[38;2;0;192;255m▀\x1b[38;2;0;189;255m▀\x1b[38;2;0;186;255m \x1b[38;2;0;184;255m▄\x1b[38;2;0;181;255m▀\x1b[38;2;0;178;255m█\x1b[38;2;0;175;255m "
    "\x1b[38;2;0;172;255m█\x1b[38;2;0;169;255m▀\x1b[38;2;0;166;255m▀\x1b[38;2;0;163;255m "
    "\x1b[38;2;0;160;255m▀\x1b[38;2;0;157;255m█\x1b[38;2;0;155;255m▀\x1b[38;2;0;152;255m "
    "\x1b[38;2;0;149;255m█\x1b[38;2;0;146;255m▀\x1b[38;2;0;143;255m▄\x1b[38;2;0;140;255m▀\x1b[38;2;0;137;255m█\x1b[38;2;0;134;255m "
    "\x1b[38;2;0;131;255m█\x1b[38;2;0;128;255m▀\x1b[38;2;0;126;255m▀\x1b[38;2;0;123;255m "
    "\x1b[38;2;0;120;255m█\x1b[38;2;0;117;255m▀\x1b[38;2;0;114;255m█\x1b[39m\n"
    "\x1b[38;2;0;198;255m \x1b[38;2;0;195;255m█\x1b[38;2;0;192;255m▀\x1b[38;2;0;189;255m \x1b[38;2;0;186;255m \x1b[38;2;0;184;255m█\x1b[38;2;0;181;255m▀\x1b[38;2;0;178;255m█\x1b[38;2;0;175;255m "
    "\x1b[38;2;0;172;255m▄\x1b[38;2;0;169;255m▄\x1b[38;2;0;166;255m█\x1b[38;2;0;163;255m "
    "\x1b[38;2;0;160;255m \x1b[38;2;0;157;255m█\x1b[38;2;0;155;255m \x1b[38;2;0;152;255m "
    "\x1b[38;2;0;149;255m█\x1b[38;2;0;146;255m \x1b[38;2;0;143;255m▀\x1b[38;2;0;140;255m \x1b[38;2;0;137;255m█\x1b[38;2;0;134;255m "
    "\x1b[38;2;0;131;255m█\x1b[38;2;0;128;255m▄\x1b[38;2;0;126;255m▄\x1b[38;2;0;123;255m "
    "\x1b[38;2;0;120;255m█\x1b[38;2;0;117;255m▀\x1b[38;2;0;114;255m▀\x1b[39m\n"
)


def log_server_banner(server: FastMCP[Any]) -> None:
    """Creates and logs a formatted banner with server information and logo."""

    # Check for updates (non-blocking, fails silently)
    newer_version = check_for_newer_version()

    # Create the logo text
    # Use Text with no_wrap and markup disabled to preserve ANSI escape codes
    logo_text = Text.from_ansi(LOGO_ASCII_4, no_wrap=True)

    # Create the main title
    title_text = Text(f"FastMCP {fastmcp.__version__}", style="bold blue")

    # Create the information table
    info_table = Table.grid(padding=(0, 1))
    info_table.add_column(style="bold", justify="center")  # Emoji column
    info_table.add_column(style="cyan", justify="left")  # Label column
    info_table.add_column(style="dim", justify="left")  # Value column

    info_table.add_row("🖥", "Server:", Text(server.name, style="dim"))
    info_table.add_row("🚀", "Deploy free:", "https://fastmcp.cloud")

    # Create panel with logo, title, and information using Group
    docs_url = Text("https://gofastmcp.com", style="dim")
    panel_content = Group(
        "",
        Align.center(logo_text),
        "",
        "",
        Align.center(title_text),
        Align.center(docs_url),
        "",
        Align.center(info_table),
    )

    # v3 notice banner (shown below main panel)
    v3_line1 = Text("✨ FastMCP 3.0 is coming!", style="bold")
    v3_line2 = Text.assemble(
        ("Pin ", "dim"),
        ("`fastmcp < 3`", "dim bold"),
        (" in production, then upgrade when you're ready.", "dim"),
    )
    v3_notice = Panel(
        Group(Align.center(v3_line1), Align.center(v3_line2)),
        border_style="blue",
        padding=(0, 2),
        width=80,
    )

    panel = Panel(
        panel_content,
        border_style="dim",
        padding=(1, 4),
        # expand=False,
        width=80,  # Set max width for the panel
    )

    console = Console(stderr=True)

    # Build output elements
    output_elements: list[Align | Panel | str] = ["\n", Align.center(panel)]
    output_elements.append(Align.center(v3_notice))

    # Add update notice if a newer version is available (shown last for visibility)
    if newer_version:
        update_line1 = Text.assemble(
            ("🎉 Update available: ", "bold"),
            (newer_version, "bold green"),
        )
        update_line2 = Text("Run: pip install --upgrade fastmcp", style="dim")
        update_notice = Panel(
            Group(Align.center(update_line1), Align.center(update_line2)),
            border_style="blue",
            padding=(0, 2),
            width=80,
        )
        output_elements.append(Align.center(update_notice))

    output_elements.append("\n")

    console.print(Group(*output_elements))
