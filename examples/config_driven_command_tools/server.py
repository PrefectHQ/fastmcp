# /// script
# dependencies = ["fastmcp"]
# ///
"""
Config-driven command tools example.

Exposes local commands defined in config.yaml as MCP tools via a Provider.
Run with: uv run fastmcp run examples/config_driven_command_tools/server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure this directory is on the path so config and command_tool_provider can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

from command_tool_provider import CommandToolProvider
from config import load_config

from fastmcp import FastMCP

config_path = Path(__file__).parent / "config.yaml"
spec = load_config(config_path)
# Resolve relative working_dir to be relative to the config file directory
config_dir = config_path.resolve().parent
for tool in spec.tools:
    if tool.working_dir is not None:
        wd = Path(tool.working_dir)
        if not wd.is_absolute():
            tool.working_dir = str((config_dir / wd).resolve())
provider = CommandToolProvider(spec)
mcp = FastMCP(spec.server_name or "Command Tools Example", providers=[provider])
