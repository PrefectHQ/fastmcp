"""Tests for MCP server discovery and name-based resolution."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from fastmcp.cli.client import _is_http_target, resolve_server_spec
from fastmcp.cli.discovery import (
    DiscoveredServer,
    _normalize_server_entry,
    _parse_mcp_config,
    _scan_claude_code,
    _scan_claude_desktop,
    _scan_cursor_workspace,
    _scan_gemini,
    _scan_goose,
    _scan_project_mcp_json,
    discover_servers,
    resolve_name,
)
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.client.transports.stdio import StdioTransport
from fastmcp.mcp_config import RemoteMCPServer, StdioMCPServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STDIO_CONFIG: dict[str, Any] = {
    "mcpServers": {
        "weather": {
            "command": "npx",
            "args": ["-y", "@mcp/weather"],
        },
        "github": {
            "command": "npx",
            "args": ["-y", "@mcp/github"],
            "env": {"GITHUB_TOKEN": "xxx"},
        },
    }
}

_REMOTE_CONFIG: dict[str, Any] = {
    "mcpServers": {
        "api": {
            "url": "http://localhost:8000/mcp",
        },
    }
}


def _write_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# DiscoveredServer properties
# ---------------------------------------------------------------------------


class TestDiscoveredServer:
    def test_qualified_name(self):
        server = DiscoveredServer(
            name="weather",
            source="claude-desktop",
            config=StdioMCPServer(command="npx", args=["-y", "@mcp/weather"]),
            config_path=Path("/fake/config.json"),
        )
        assert server.qualified_name == "claude-desktop:weather"

    def test_transport_summary_stdio(self):
        server = DiscoveredServer(
            name="weather",
            source="cursor",
            config=StdioMCPServer(command="npx", args=["-y", "@mcp/weather"]),
            config_path=Path("/fake/config.json"),
        )
        assert server.transport_summary == "stdio: npx -y @mcp/weather"

    def test_transport_summary_remote(self):
        server = DiscoveredServer(
            name="api",
            source="project",
            config=RemoteMCPServer(url="http://localhost:8000/mcp"),
            config_path=Path("/fake/config.json"),
        )
        assert server.transport_summary == "http: http://localhost:8000/mcp"

    def test_transport_summary_remote_sse(self):
        server = DiscoveredServer(
            name="api",
            source="project",
            config=RemoteMCPServer(url="http://localhost:8000/sse", transport="sse"),
            config_path=Path("/fake/config.json"),
        )
        assert server.transport_summary == "sse: http://localhost:8000/sse"


# ---------------------------------------------------------------------------
# _parse_mcp_config
# ---------------------------------------------------------------------------


class TestParseMcpConfig:
    def test_valid_config(self, tmp_path: Path):
        path = tmp_path / "config.json"
        _write_config(path, _STDIO_CONFIG)
        servers = _parse_mcp_config(path, "test-source")
        assert len(servers) == 2
        names = {s.name for s in servers}
        assert names == {"weather", "github"}
        assert all(s.source == "test-source" for s in servers)
        assert all(s.config_path == path for s in servers)

    def test_missing_file(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        servers = _parse_mcp_config(path, "test")
        assert servers == []

    def test_invalid_json(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        servers = _parse_mcp_config(path, "test")
        assert servers == []

    def test_no_mcp_servers_key(self, tmp_path: Path):
        path = tmp_path / "config.json"
        _write_config(path, {"something": "else"})
        servers = _parse_mcp_config(path, "test")
        assert servers == []

    def test_empty_mcp_servers(self, tmp_path: Path):
        path = tmp_path / "config.json"
        _write_config(path, {"mcpServers": {}})
        servers = _parse_mcp_config(path, "test")
        assert servers == []

    def test_remote_server(self, tmp_path: Path):
        path = tmp_path / "config.json"
        _write_config(path, _REMOTE_CONFIG)
        servers = _parse_mcp_config(path, "test")
        assert len(servers) == 1
        assert isinstance(servers[0].config, RemoteMCPServer)
        assert servers[0].config.url == "http://localhost:8000/mcp"


# ---------------------------------------------------------------------------
# Scanner: Claude Desktop
# ---------------------------------------------------------------------------


class TestScanClaudeDesktop:
    def test_finds_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        config_dir = tmp_path / "Claude"
        config_path = config_dir / "claude_desktop_config.json"
        _write_config(config_path, _STDIO_CONFIG)

        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        # Force darwin for deterministic path
        monkeypatch.setattr("fastmcp.cli.discovery.sys.platform", "darwin")

        # We need to override the path construction. On macOS it's
        # ~/Library/Application Support/Claude — create that.
        mac_dir = tmp_path / "Library" / "Application Support" / "Claude"
        mac_path = mac_dir / "claude_desktop_config.json"
        _write_config(mac_path, _STDIO_CONFIG)

        servers = _scan_claude_desktop()
        assert len(servers) == 2
        assert all(s.source == "claude-desktop" for s in servers)

    def test_missing_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("fastmcp.cli.discovery.sys.platform", "darwin")
        servers = _scan_claude_desktop()
        assert servers == []


# ---------------------------------------------------------------------------
# Normalize server entry
# ---------------------------------------------------------------------------


class TestNormalizeServerEntry:
    def test_remote_type_becomes_transport(self):
        entry = {"url": "http://localhost:8000/sse", "type": "sse"}
        result = _normalize_server_entry(entry)
        assert result["transport"] == "sse"
        assert "type" not in result

    def test_remote_with_transport_unchanged(self):
        entry = {"url": "http://localhost:8000/mcp", "transport": "http"}
        result = _normalize_server_entry(entry)
        assert result["transport"] == "http"

    def test_stdio_type_unchanged(self):
        """Stdio entries have ``type`` as a proper field — leave it alone."""
        entry = {"command": "npx", "args": [], "type": "stdio"}
        result = _normalize_server_entry(entry)
        assert result["type"] == "stdio"

    def test_gemini_http_url_becomes_url(self):
        entry = {"httpUrl": "https://api.example.com/mcp/"}
        result = _normalize_server_entry(entry)
        assert result["url"] == "https://api.example.com/mcp/"
        assert "httpUrl" not in result

    def test_gemini_http_url_does_not_override_url(self):
        entry = {"url": "http://real.com", "httpUrl": "http://other.com"}
        result = _normalize_server_entry(entry)
        assert result["url"] == "http://real.com"


# ---------------------------------------------------------------------------
# Scanner: Claude Code
# ---------------------------------------------------------------------------


def _claude_code_config(
    *,
    global_servers: dict[str, Any] | None = None,
    project_path: str | None = None,
    project_servers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal ~/.claude.json structure."""
    data: dict[str, Any] = {}
    if global_servers is not None:
        data["mcpServers"] = global_servers
    if project_path and project_servers is not None:
        data["projects"] = {project_path: {"mcpServers": project_servers}}
    return data


class TestScanClaudeCode:
    def test_global_servers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        config_path = tmp_path / ".claude.json"
        _write_config(
            config_path,
            _claude_code_config(global_servers=_STDIO_CONFIG["mcpServers"]),
        )
        servers = _scan_claude_code(tmp_path)
        assert len(servers) == 2
        assert all(s.source == "claude-code" for s in servers)

    def test_project_servers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        config_path = tmp_path / ".claude.json"
        _write_config(
            config_path,
            _claude_code_config(
                project_path=str(project_dir),
                project_servers={"api": {"url": "http://localhost:8000/mcp"}},
            ),
        )
        servers = _scan_claude_code(project_dir)
        assert len(servers) == 1
        assert servers[0].name == "api"

    def test_global_and_project_combined(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        config_path = tmp_path / ".claude.json"
        _write_config(
            config_path,
            _claude_code_config(
                global_servers={"global-tool": {"command": "echo", "args": ["hi"]}},
                project_path=str(project_dir),
                project_servers={"local-tool": {"command": "cat", "args": []}},
            ),
        )
        servers = _scan_claude_code(project_dir)
        names = {s.name for s in servers}
        assert names == {"global-tool", "local-tool"}

    def test_type_normalized_to_transport(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Claude Code uses ``type: sse`` — verify it becomes ``transport``."""
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        config_path = tmp_path / ".claude.json"
        _write_config(
            config_path,
            _claude_code_config(
                global_servers={
                    "sse-server": {
                        "type": "sse",
                        "url": "http://localhost:8000/sse",
                    }
                }
            ),
        )
        servers = _scan_claude_code(tmp_path)
        assert len(servers) == 1
        assert isinstance(servers[0].config, RemoteMCPServer)
        assert servers[0].config.transport == "sse"

    def test_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        servers = _scan_claude_code(tmp_path)
        assert servers == []

    def test_no_matching_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        config_path = tmp_path / ".claude.json"
        _write_config(
            config_path,
            _claude_code_config(
                project_path="/some/other/project",
                project_servers={"tool": {"command": "echo", "args": []}},
            ),
        )
        servers = _scan_claude_code(tmp_path)
        assert servers == []


# ---------------------------------------------------------------------------
# Scanner: Cursor workspace
# ---------------------------------------------------------------------------


class TestScanCursorWorkspace:
    def test_finds_config_in_cwd(self, tmp_path: Path):
        cursor_path = tmp_path / ".cursor" / "mcp.json"
        _write_config(cursor_path, _STDIO_CONFIG)
        servers = _scan_cursor_workspace(tmp_path)
        assert len(servers) == 2
        assert all(s.source == "cursor" for s in servers)

    def test_finds_config_in_parent(self, tmp_path: Path):
        cursor_path = tmp_path / ".cursor" / "mcp.json"
        _write_config(cursor_path, _STDIO_CONFIG)
        child = tmp_path / "src" / "deep"
        child.mkdir(parents=True)
        servers = _scan_cursor_workspace(child)
        assert len(servers) == 2

    def test_stops_at_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Place config above home — should not be found
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        above_home = tmp_path.parent / ".cursor" / "mcp.json"
        _write_config(above_home, _STDIO_CONFIG)
        child = tmp_path / "project"
        child.mkdir()
        servers = _scan_cursor_workspace(child)
        assert servers == []

    def test_no_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Confine walk to tmp_path so it doesn't find sibling test dirs
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        servers = _scan_cursor_workspace(tmp_path)
        assert servers == []


# ---------------------------------------------------------------------------
# Scanner: project mcp.json
# ---------------------------------------------------------------------------


class TestScanProjectMcpJson:
    def test_finds_config(self, tmp_path: Path):
        config_path = tmp_path / "mcp.json"
        _write_config(config_path, _STDIO_CONFIG)
        servers = _scan_project_mcp_json(tmp_path)
        assert len(servers) == 2
        assert all(s.source == "project" for s in servers)

    def test_no_config(self, tmp_path: Path):
        servers = _scan_project_mcp_json(tmp_path)
        assert servers == []


# ---------------------------------------------------------------------------
# Scanner: Gemini CLI
# ---------------------------------------------------------------------------


class TestScanGemini:
    def test_user_level_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        config_path = tmp_path / ".gemini" / "settings.json"
        _write_config(config_path, _STDIO_CONFIG)
        servers = _scan_gemini(tmp_path)
        assert len(servers) == 2
        assert all(s.source == "gemini" for s in servers)

    def test_project_level_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        config_path = project_dir / ".gemini" / "settings.json"
        _write_config(config_path, _STDIO_CONFIG)
        servers = _scan_gemini(project_dir)
        assert len(servers) == 2

    def test_http_url_normalized(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Gemini uses ``httpUrl`` — verify it becomes ``url``."""
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        config_path = tmp_path / ".gemini" / "settings.json"
        _write_config(
            config_path,
            {
                "mcpServers": {
                    "api": {"httpUrl": "https://api.example.com/mcp/"},
                }
            },
        )
        servers = _scan_gemini(tmp_path)
        assert len(servers) == 1
        assert isinstance(servers[0].config, RemoteMCPServer)
        assert servers[0].config.url == "https://api.example.com/mcp/"

    def test_missing_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        servers = _scan_gemini(tmp_path)
        assert servers == []


# ---------------------------------------------------------------------------
# Scanner: Goose
# ---------------------------------------------------------------------------

_GOOSE_CONFIG = {
    "extensions": {
        "developer": {
            "enabled": True,
            "name": "developer",
            "type": "builtin",
        },
        "tavily": {
            "cmd": "npx",
            "args": ["-y", "mcp-tavily-search"],
            "enabled": True,
            "envs": {"TAVILY_API_KEY": "xxx"},
            "type": "stdio",
        },
        "disabled-tool": {
            "cmd": "echo",
            "args": ["hi"],
            "enabled": False,
            "type": "stdio",
        },
    }
}


class TestScanGoose:
    def test_finds_stdio_extensions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        config_dir = tmp_path / ".config" / "goose"
        config_path = config_dir / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.dump(_GOOSE_CONFIG))
        # Force non-windows platform for path logic
        monkeypatch.setattr("fastmcp.cli.discovery.sys.platform", "linux")
        servers = _scan_goose()
        assert len(servers) == 1
        assert servers[0].name == "tavily"
        assert servers[0].source == "goose"
        assert isinstance(servers[0].config, StdioMCPServer)
        assert servers[0].config.command == "npx"

    def test_skips_builtin_and_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        config_dir = tmp_path / ".config" / "goose"
        config_path = config_dir / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.dump(_GOOSE_CONFIG))
        monkeypatch.setattr("fastmcp.cli.discovery.sys.platform", "linux")
        servers = _scan_goose()
        names = {s.name for s in servers}
        assert "developer" not in names
        assert "disabled-tool" not in names

    def test_missing_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("fastmcp.cli.discovery.sys.platform", "linux")
        servers = _scan_goose()
        assert servers == []


# ---------------------------------------------------------------------------
# discover_servers
# ---------------------------------------------------------------------------


def _suppress_user_scanners(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress all scanners that read real user config files."""
    monkeypatch.setattr("fastmcp.cli.discovery._scan_claude_desktop", lambda: [])
    monkeypatch.setattr("fastmcp.cli.discovery._scan_claude_code", lambda start_dir: [])
    monkeypatch.setattr("fastmcp.cli.discovery._scan_gemini", lambda start_dir: [])
    monkeypatch.setattr("fastmcp.cli.discovery._scan_goose", lambda: [])


class TestDiscoverServers:
    def test_combines_sources(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Set up project mcp.json
        project_config = tmp_path / "mcp.json"
        _write_config(project_config, _STDIO_CONFIG)

        # Set up cursor config
        cursor_config = tmp_path / ".cursor" / "mcp.json"
        _write_config(cursor_config, _REMOTE_CONFIG)

        _suppress_user_scanners(monkeypatch)

        servers = discover_servers(start_dir=tmp_path)
        sources = {s.source for s in servers}
        assert "project" in sources
        assert "cursor" in sources
        assert len(servers) == 3  # 2 from project + 1 from cursor

    def test_preserves_duplicates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Same server name in multiple sources should appear multiple times."""
        project_config = tmp_path / "mcp.json"
        _write_config(project_config, _STDIO_CONFIG)

        cursor_config = tmp_path / ".cursor" / "mcp.json"
        _write_config(cursor_config, _STDIO_CONFIG)

        _suppress_user_scanners(monkeypatch)

        servers = discover_servers(start_dir=tmp_path)
        weather_servers = [s for s in servers if s.name == "weather"]
        assert len(weather_servers) == 2
        assert {s.source for s in weather_servers} == {"cursor", "project"}


# ---------------------------------------------------------------------------
# resolve_name
# ---------------------------------------------------------------------------


class TestResolveName:
    @pytest.fixture(autouse=True)
    def _isolate_scanners(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Suppress scanners that read real user configs and confine walks to tmp_path."""
        _suppress_user_scanners(monkeypatch)
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)

    def test_unique_match(self, tmp_path: Path):
        config_path = tmp_path / "mcp.json"
        _write_config(config_path, _STDIO_CONFIG)
        transport = resolve_name("weather", start_dir=tmp_path)
        assert isinstance(transport, StdioTransport)

    def test_qualified_match(self, tmp_path: Path):
        config_path = tmp_path / "mcp.json"
        _write_config(config_path, _STDIO_CONFIG)
        transport = resolve_name("project:weather", start_dir=tmp_path)
        assert isinstance(transport, StdioTransport)

    def test_not_found_with_servers(self, tmp_path: Path):
        config_path = tmp_path / "mcp.json"
        _write_config(config_path, _STDIO_CONFIG)
        with pytest.raises(ValueError, match="No server named 'nope'.*Available"):
            resolve_name("nope", start_dir=tmp_path)

    def test_not_found_no_servers(self, tmp_path: Path):
        with pytest.raises(ValueError, match="No server named 'nope'.*Searched"):
            resolve_name("nope", start_dir=tmp_path)

    def test_ambiguous_name(self, tmp_path: Path):
        project_config = tmp_path / "mcp.json"
        _write_config(project_config, _STDIO_CONFIG)
        cursor_config = tmp_path / ".cursor" / "mcp.json"
        _write_config(cursor_config, _STDIO_CONFIG)
        with pytest.raises(ValueError, match="Ambiguous server name 'weather'"):
            resolve_name("weather", start_dir=tmp_path)

    def test_ambiguous_resolved_by_qualified(self, tmp_path: Path):
        project_config = tmp_path / "mcp.json"
        _write_config(project_config, _STDIO_CONFIG)
        cursor_config = tmp_path / ".cursor" / "mcp.json"
        _write_config(cursor_config, _STDIO_CONFIG)
        transport = resolve_name("cursor:weather", start_dir=tmp_path)
        assert isinstance(transport, StdioTransport)

    def test_qualified_not_found(self, tmp_path: Path):
        config_path = tmp_path / "mcp.json"
        _write_config(config_path, _STDIO_CONFIG)
        with pytest.raises(
            ValueError, match="No server named 'nope' found in source 'project'"
        ):
            resolve_name("project:nope", start_dir=tmp_path)

    def test_remote_server_resolves_to_http_transport(self, tmp_path: Path):
        config_path = tmp_path / "mcp.json"
        _write_config(config_path, _REMOTE_CONFIG)
        transport = resolve_name("api", start_dir=tmp_path)
        assert isinstance(transport, StreamableHttpTransport)


# ---------------------------------------------------------------------------
# Integration: resolve_server_spec falls through to name resolution
# ---------------------------------------------------------------------------


class TestResolveServerSpecNameFallback:
    def test_bare_name_resolves(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        config_path = tmp_path / "mcp.json"
        _write_config(config_path, _STDIO_CONFIG)
        _suppress_user_scanners(monkeypatch)
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)

        # Monkeypatch resolve_name in client module to use our tmp_path
        original_resolve = resolve_name

        def patched_resolve(name: str, start_dir: Path | None = None) -> Any:
            return original_resolve(name, start_dir=tmp_path)

        monkeypatch.setattr("fastmcp.cli.client.resolve_name", patched_resolve)

        result = resolve_server_spec("weather")
        assert isinstance(result, StdioTransport)

    def test_url_takes_priority_over_name(self):
        """URLs should be resolved before name lookup."""
        result = resolve_server_spec("http://localhost:8000/mcp")
        assert result == "http://localhost:8000/mcp"


# ---------------------------------------------------------------------------
# Integration: _is_http_target detects transport objects
# ---------------------------------------------------------------------------


class TestIsHttpTargetTransports:
    def test_streamable_http_transport(self):
        transport = StreamableHttpTransport("http://localhost:8000/mcp")
        assert _is_http_target(transport) is True

    def test_sse_transport(self):
        transport = SSETransport("http://localhost:8000/sse")
        assert _is_http_target(transport) is True

    def test_stdio_transport(self):
        transport = StdioTransport(command="echo", args=["hello"])
        assert _is_http_target(transport) is False

    def test_string_url(self):
        assert _is_http_target("http://localhost:8000") is True

    def test_string_non_url(self):
        assert _is_http_target("server.py") is False

    def test_dict_config(self):
        assert _is_http_target({"mcpServers": {}}) is False
