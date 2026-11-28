"""Regression tests for GH-6853: CLI redirected stdout UTF-8 encoding on Windows."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from fastmcp.cli.cli import _ensure_utf8_stdio, app


def test_ensure_utf8_stdio_reconfigures_cp1252_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirected cp1252 streams on Windows are upgraded cleanly to UTF-8."""
    stdout_buf = io.BytesIO()
    stderr_buf = io.BytesIO()
    fake_stdout = io.TextIOWrapper(stdout_buf, encoding="cp1252")
    fake_stderr = io.TextIOWrapper(stderr_buf, encoding="cp1252")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(sys, "stderr", fake_stderr)

    _ensure_utf8_stdio()

    assert fake_stdout.encoding.lower() == "utf-8"
    assert fake_stderr.encoding.lower() == "utf-8"

    sample = "\u2713 مرحبا"
    fake_stdout.write(sample)
    fake_stdout.flush()
    assert stdout_buf.getvalue().decode("utf-8") == sample


def test_app_call_reconfigures_cp1252_stdout_for_rich_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoking the CLI app on cp1252 stdout prints Unicode checkmarks cleanly."""
    stdout_buf = io.BytesIO()
    fake_stdout = io.TextIOWrapper(stdout_buf, encoding="cp1252")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    with patch("fastmcp.cli.cli.pyperclip.copy"):
        app(["version", "--copy"], result_action="return_value")

    fake_stdout.flush()
    rendered = stdout_buf.getvalue().decode("utf-8")
    assert "\u2713" in rendered
    assert "Version information copied to clipboard" in rendered


def test_ensure_utf8_stdio_ignores_non_reconfigurable_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom capture streams without reconfigure (e.g. StringIO) must not fail."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", None)

    _ensure_utf8_stdio()


@pytest.mark.subprocess_heavy
@pytest.mark.timeout(30)
@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Redirected CLI stdout defaults to ANSI code page on Windows",
)
def test_redirected_generate_cli_succeeds_without_pythonutf8(tmp_path: Path) -> None:
    """Subprocess CLI invocation with redirected stdout exits 0 on Windows."""
    env = os.environ.copy()
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)

    server_file = tmp_path / "srv.py"
    server_file.write_text(
        'from fastmcp import FastMCP\n\nmcp = FastMCP("demo")\n\n'
        '@mcp.tool\ndef ping() -> str:\n    """مرحبا \u2713"""\n    return "pong"\n',
        encoding="utf-8",
    )
    cli_out = tmp_path / "cli.py"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastmcp.cli",
            "generate-cli",
            str(server_file),
            "--output",
            str(cli_out),
            "--force",
        ],
        env=env,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert cli_out.exists()
    assert "\u2713".encode() in proc.stdout
