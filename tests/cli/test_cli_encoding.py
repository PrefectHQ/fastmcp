"""Regression tests for CLI output encoding."""

import io
import os
import subprocess
import sys

from fastmcp.cli import _configure_output_streams


def test_configure_output_streams_replaces_unencodable_characters(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    _configure_output_streams()

    assert stream.errors == "replace"
    stream.write("\u2713")
    stream.flush()


def test_cli_survives_redirected_cp1252_output():
    script = (
        "import fastmcp.cli\n"
        "from rich.console import Console\n"
        "Console().print('[green]\\u2713[/green] redirected output works')\n"
    )
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "redirected output works" in result.stdout
