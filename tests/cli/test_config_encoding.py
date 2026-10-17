"""Regression tests for GH-5317: CLI config files are UTF-8 regardless of locale."""

import builtins
import io
import json
from pathlib import Path
from typing import Any

import pytest

from fastmcp.cli.client import _resolve_json_spec
from fastmcp.cli.install.claude_desktop import install_claude_desktop
from fastmcp.cli.install.shared import process_common_args
from fastmcp.cli.run import create_mcp_config_server
from fastmcp.utilities.cli import load_and_merge_config

ARABIC_PATH = "C:\\Users\\فاطمة\\notes.js"
GREETING = "Olá José"


@pytest.fixture
def cp1252_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make text reads without an explicit encoding decode as cp1252, as they
    do on Windows with a non-UTF-8 system code page."""
    real_open = builtins.open

    def locale_open(
        file: Any,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if "b" not in mode and encoding in (None, "locale"):
            encoding = "cp1252"
        return real_open(file, mode, buffering, encoding, *args, **kwargs)

    def text_encoding(encoding: str | None, stacklevel: int = 2) -> str:
        return encoding or "cp1252"

    monkeypatch.setattr(builtins, "open", locale_open)
    monkeypatch.setattr(io, "text_encoding", text_encoding)


def _write_utf8_json(path: Path, data: dict[str, Any]) -> None:
    path.write_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _mcp_config() -> dict[str, Any]:
    return {
        "mcpServers": {
            "demo": {
                "command": "node",
                "args": [ARABIC_PATH],
                "env": {"GREETING": GREETING},
            }
        }
    }


@pytest.mark.usefixtures("cp1252_locale")
class TestConfigFilesReadAsUtf8:
    def test_client_resolves_mcp_config(self, tmp_path: Path):
        path = tmp_path / "mcp.json"
        _write_utf8_json(path, _mcp_config())

        assert _resolve_json_spec(path) == _mcp_config()

    def test_run_creates_mcp_config_server(self, tmp_path: Path):
        path = tmp_path / "mcp.json"
        _write_utf8_json(path, _mcp_config())

        create_mcp_config_server(path)

    def test_load_and_merge_fastmcp_json(self, tmp_path: Path):
        path = tmp_path / "fastmcp.json"
        _write_utf8_json(path, {"source": {"path": ARABIC_PATH}})

        config, _ = load_and_merge_config(str(path))

        assert config.source.path == ARABIC_PATH

    async def test_install_reads_fastmcp_json(self, tmp_path: Path):
        path = tmp_path / "fastmcp.json"
        (tmp_path / "فاطمة.py").write_text("", encoding="utf-8")
        _write_utf8_json(path, {"source": {"path": "فاطمة.py"}})

        file, *_ = await process_common_args(str(path), None, None, None, None)

        assert file == tmp_path / "فاطمة.py"

    def test_install_claude_desktop_merges_existing_config(self, tmp_path: Path):
        config_file = tmp_path / "claude_desktop_config.json"
        _write_utf8_json(config_file, _mcp_config())

        assert install_claude_desktop(
            tmp_path / "server.py", None, "demo", config_path=tmp_path
        )

        config = json.loads(config_file.read_bytes().decode("utf-8"))
        assert config["mcpServers"]["demo"]["env"] == {"GREETING": GREETING}
