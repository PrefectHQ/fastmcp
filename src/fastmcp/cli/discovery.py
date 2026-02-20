"""Discover MCP servers configured in editor config files.

Scans filesystem-readable config files from editors like Claude Desktop,
Claude Code, Cursor, Gemini CLI, and Goose, as well as project-level
``mcp.json`` files. Each discovered server can be resolved by name
(or ``source:name``) so the CLI can connect without requiring a URL
or file path.
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from fastmcp.client.transports.base import ClientTransport
from fastmcp.mcp_config import (
    MCPConfig,
    MCPServerTypes,
    RemoteMCPServer,
    StdioMCPServer,
)
from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.discovery")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveredServer:
    """A single MCP server found in an editor or project config."""

    name: str
    source: str
    config: MCPServerTypes
    config_path: Path

    @property
    def qualified_name(self) -> str:
        """Fully qualified ``source:name`` identifier."""
        return f"{self.source}:{self.name}"

    @property
    def transport_summary(self) -> str:
        """Human-readable one-liner describing the transport."""
        cfg = self.config
        if isinstance(cfg, StdioMCPServer):
            parts = [cfg.command, *cfg.args]
            return f"stdio: {' '.join(parts)}"
        if isinstance(cfg, RemoteMCPServer):
            transport = cfg.transport or "http"
            return f"{transport}: {cfg.url}"
        return str(type(cfg).__name__)


# ---------------------------------------------------------------------------
# Scanners — one per config source
# ---------------------------------------------------------------------------


def _normalize_server_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize editor-specific server config fields to MCPConfig format.

    Handles two known differences:
    - Claude Code uses ``type`` where MCPConfig uses ``transport`` for
      remote servers.
    - Gemini CLI uses ``httpUrl`` where MCPConfig uses ``url``.
    """
    # Gemini: httpUrl → url
    if "httpUrl" in entry and "url" not in entry:
        entry = {**entry, "url": entry["httpUrl"]}
        del entry["httpUrl"]

    # Claude Code / others: type → transport (for url-based entries only)
    if "url" in entry and "type" in entry and "transport" not in entry:
        transport = entry["type"]
        entry = {k: v for k, v in entry.items() if k != "type"}
        entry["transport"] = transport

    return entry


def _parse_mcp_servers(
    servers_dict: dict[str, Any],
    *,
    source: str,
    config_path: Path,
) -> list[DiscoveredServer]:
    """Parse an ``mcpServers``-style dict into discovered servers."""
    if not servers_dict:
        return []

    normalized = {
        name: _normalize_server_entry(entry)
        for name, entry in servers_dict.items()
        if isinstance(entry, dict)
    }

    try:
        config = MCPConfig.from_dict({"mcpServers": normalized})
    except Exception as exc:
        logger.warning("Could not parse MCP servers from %s: %s", config_path, exc)
        return []

    return [
        DiscoveredServer(
            name=name, source=source, config=server, config_path=config_path
        )
        for name, server in config.mcpServers.items()
    ]


def _parse_mcp_config(path: Path, source: str) -> list[DiscoveredServer]:
    """Parse an mcpServers-style JSON file into discovered servers."""
    try:
        text = path.read_text()
    except OSError as exc:
        logger.debug("Could not read %s: %s", path, exc)
        return []

    try:
        data: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON in %s: %s", path, exc)
        return []

    if not isinstance(data, dict) or "mcpServers" not in data:
        return []

    return _parse_mcp_servers(data["mcpServers"], source=source, config_path=path)


def _scan_claude_desktop() -> list[DiscoveredServer]:
    """Scan the Claude Desktop config file."""
    if sys.platform == "win32":
        config_dir = Path(Path.home(), "AppData", "Roaming", "Claude")
    elif sys.platform == "darwin":
        config_dir = Path(Path.home(), "Library", "Application Support", "Claude")
    elif sys.platform.startswith("linux"):
        config_dir = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"), "Claude"
        )
    else:
        return []

    path = config_dir / "claude_desktop_config.json"
    return _parse_mcp_config(path, "claude-desktop")


def _scan_claude_code(start_dir: Path) -> list[DiscoveredServer]:
    """Scan ``~/.claude.json`` for global and project-scoped MCP servers."""
    path = Path.home() / ".claude.json"
    try:
        text = path.read_text()
    except OSError:
        return []

    try:
        data: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON in %s: %s", path, exc)
        return []

    if not isinstance(data, dict):
        return []

    results: list[DiscoveredServer] = []

    # Global servers
    if global_servers := data.get("mcpServers"):
        if isinstance(global_servers, dict):
            results.extend(
                _parse_mcp_servers(
                    global_servers, source="claude-code", config_path=path
                )
            )

    # Project-scoped servers matching start_dir
    resolved_dir = str(start_dir.resolve())
    projects = data.get("projects", {})
    if isinstance(projects, dict):
        project_data = projects.get(resolved_dir, {})
        if isinstance(project_data, dict):
            if project_servers := project_data.get("mcpServers"):
                if isinstance(project_servers, dict):
                    results.extend(
                        _parse_mcp_servers(
                            project_servers,
                            source="claude-code",
                            config_path=path,
                        )
                    )

    return results


def _scan_cursor_workspace(start_dir: Path) -> list[DiscoveredServer]:
    """Walk up from *start_dir* looking for ``.cursor/mcp.json``."""
    current = start_dir.resolve()
    home = Path.home().resolve()

    while True:
        candidate = current / ".cursor" / "mcp.json"
        if candidate.is_file():
            return _parse_mcp_config(candidate, "cursor")

        parent = current.parent
        # Stop at filesystem root or home directory
        if parent == current or current == home:
            break
        current = parent

    return []


def _scan_project_mcp_json(start_dir: Path) -> list[DiscoveredServer]:
    """Check for ``mcp.json`` in *start_dir*."""
    candidate = start_dir.resolve() / "mcp.json"
    if candidate.is_file():
        return _parse_mcp_config(candidate, "project")
    return []


def _scan_gemini(start_dir: Path) -> list[DiscoveredServer]:
    """Scan Gemini CLI settings for MCP servers.

    Checks both user-level ``~/.gemini/settings.json`` and project-level
    ``.gemini/settings.json``.
    """
    results: list[DiscoveredServer] = []

    # User-level
    user_path = Path.home() / ".gemini" / "settings.json"
    results.extend(_parse_mcp_config(user_path, "gemini"))

    # Project-level
    project_path = start_dir.resolve() / ".gemini" / "settings.json"
    if project_path != user_path:
        results.extend(_parse_mcp_config(project_path, "gemini"))

    return results


def _scan_goose() -> list[DiscoveredServer]:
    """Scan Goose config for MCP server extensions.

    Goose uses YAML (``~/.config/goose/config.yaml``) with a different
    schema — MCP servers are defined as ``extensions`` with ``type: stdio``.
    """
    if sys.platform == "win32":
        config_dir = Path(
            os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"),
            "Block",
            "goose",
            "config",
        )
    else:
        config_dir = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"),
            "goose",
        )

    path = config_dir / "config.yaml"
    try:
        text = path.read_text()
    except OSError:
        return []

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning("Invalid YAML in %s: %s", path, exc)
        return []

    if not isinstance(data, dict):
        return []

    extensions = data.get("extensions", {})
    if not isinstance(extensions, dict):
        return []

    # Convert Goose extensions to mcpServers format
    servers: dict[str, Any] = {}
    for name, ext in extensions.items():
        if not isinstance(ext, dict):
            continue
        if not ext.get("enabled", True):
            continue
        ext_type = ext.get("type", "")
        if ext_type == "stdio" and "cmd" in ext:
            servers[name] = {
                "command": ext["cmd"],
                "args": ext.get("args", []),
                "env": ext.get("envs", {}),
            }
        elif ext_type == "sse" and "uri" in ext:
            servers[name] = {"url": ext["uri"], "transport": "sse"}

    return _parse_mcp_servers(servers, source="goose", config_path=path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_servers(start_dir: Path | None = None) -> list[DiscoveredServer]:
    """Run all scanners and return the combined results.

    Duplicate names across sources are preserved — callers can
    use :pyattr:`DiscoveredServer.qualified_name` to disambiguate.
    """
    cwd = start_dir or Path.cwd()
    results: list[DiscoveredServer] = []
    results.extend(_scan_claude_desktop())
    results.extend(_scan_claude_code(cwd))
    results.extend(_scan_cursor_workspace(cwd))
    results.extend(_scan_gemini(cwd))
    results.extend(_scan_goose())
    results.extend(_scan_project_mcp_json(cwd))
    return results


def resolve_name(name: str, start_dir: Path | None = None) -> ClientTransport:
    """Resolve a server name (or ``source:name``) to a transport.

    Raises :class:`ValueError` when the name is not found or is ambiguous.
    """
    servers = discover_servers(start_dir)

    # Qualified form: "cursor:weather"
    if ":" in name:
        source, server_name = name.split(":", 1)
        matches = [s for s in servers if s.source == source and s.name == server_name]
        if not matches:
            raise ValueError(
                f"No server named '{server_name}' found in source '{source}'."
            )
        return matches[0].config.to_transport()

    # Bare name: "weather"
    matches = [s for s in servers if s.name == name]

    if not matches:
        if servers:
            available = ", ".join(sorted({s.name for s in servers}))
            raise ValueError(f"No server named '{name}' found. Available: {available}")
        locations = [
            "Claude Desktop config",
            "~/.claude.json (Claude Code)",
            ".cursor/mcp.json (walked up from cwd)",
            "~/.gemini/settings.json (Gemini CLI)",
            "~/.config/goose/config.yaml (Goose)",
            "./mcp.json",
        ]
        raise ValueError(
            f"No server named '{name}' found. Searched: {', '.join(locations)}"
        )

    if len(matches) == 1:
        return matches[0].config.to_transport()

    # Ambiguous — list qualified alternatives
    alternatives = ", ".join(f"'{m.qualified_name}'" for m in matches)
    raise ValueError(
        f"Ambiguous server name '{name}' — found in multiple sources. "
        f"Use a qualified name: {alternatives}"
    )
