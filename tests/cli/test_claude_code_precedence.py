"""Claude Code local scope wins without losing other discovery sources."""

import json
from pathlib import Path
from typing import Any

import pytest

from fastmcp.cli.discovery import _scan_claude_code, resolve_name
from fastmcp.client.transports.stdio import StdioTransport


@pytest.fixture
def claude_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    project = home / "project"
    project.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    def configure(user: dict[str, Any], local: dict[str, Any]) -> Path:
        (home / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": user,
                    "projects": {str(project.resolve()): {"mcpServers": local}},
                }
            )
        )
        return project

    return configure


def test_local_wins_for_bare_and_qualified_names(claude_project) -> None:
    project = claude_project(
        {"weather": {"command": "user-weather", "env": {"LOWER": "user-only"}}},
        {"weather": {"command": "local-weather"}},
    )
    found = _scan_claude_code(project)
    assert len(found) == 1
    assert found[0].qualified_name == "claude-code:weather"
    for name in ("weather", "claude-code:weather"):
        config = resolve_name(name, project)
        assert isinstance(config, StdioTransport)
        assert config.command == "local-weather"
        assert config.env == {}


@pytest.mark.parametrize(
    "user,local,expected",
    [
        ({"user": {"command": "u"}}, {"local": {"command": "l"}}, {"user", "local"}),
        ({"weather": {"command": "u"}}, {}, {"weather"}),
        ({}, {"weather": {"command": "l"}}, {"weather"}),
        (
            {"Weather": {"command": "u"}},
            {"weather": {"command": "l"}},
            {"Weather", "weather"},
        ),
    ],
)
def test_distinct_names_survive(claude_project, user, local, expected) -> None:
    assert {
        server.name for server in _scan_claude_code(claude_project(user, local))
    } == expected


def test_remote_local_overrides_stdio_user(claude_project) -> None:
    project = claude_project(
        {"weather": {"command": "user-weather"}},
        {"weather": {"url": "https://example.test/mcp"}},
    )
    found = _scan_claude_code(project)
    assert len(found) == 1
    assert str(getattr(found[0].config, "url")) == "https://example.test/mcp"


def test_other_source_remains_ambiguous(claude_project) -> None:
    project = claude_project(
        {"weather": {"command": "u"}}, {"weather": {"command": "l"}}
    )
    cursor = project / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "cursor"}}})
    )
    with pytest.raises(ValueError, match="Ambiguous"):
        resolve_name("weather", project)
    for name, expected in (("claude-code:weather", "l"), ("cursor:weather", "cursor")):
        config = resolve_name(name, project)
        assert isinstance(config, StdioTransport)
        assert config.command == expected


def test_invalid_local_keeps_valid_user(claude_project) -> None:
    project = claude_project(
        {"weather": {"command": "u"}}, {"weather": {"command": ["invalid"]}}
    )
    config = resolve_name("weather", project)
    assert isinstance(config, StdioTransport)
    assert config.command == "u"
