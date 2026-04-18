"""Tests for the `fastmcp plugin` CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def _run_fastmcp(
    *args: str, cwd: Path, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke the `fastmcp` CLI as a subprocess."""
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = str(cwd)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "fastmcp.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


class TestManifestCLI:
    def test_manifest_for_top_level_class(self, tmp_path: Path):
        (tmp_path / "demo.py").write_text(
            textwrap.dedent(
                """
                from fastmcp.server.plugins import Plugin, PluginMeta

                class Demo(Plugin):
                    meta = PluginMeta(name="demo", version="0.1.0")
                """
            )
        )
        result = _run_fastmcp("plugin", "manifest", "demo:Demo", cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        manifest = json.loads(result.stdout)
        assert manifest["name"] == "demo"
        assert manifest["entry_point"] == "demo:Demo"

    def test_manifest_for_nested_class(self, tmp_path: Path):
        """`__qualname__` produces dotted paths for nested classes; the CLI
        must traverse the dots to resolve the inner class."""
        (tmp_path / "demo.py").write_text(
            textwrap.dedent(
                """
                from fastmcp.server.plugins import Plugin, PluginMeta

                class Outer:
                    class Inner(Plugin):
                        meta = PluginMeta(name="inner", version="0.1.0")
                """
            )
        )
        result = _run_fastmcp(
            "plugin", "manifest", "demo:Outer.Inner", cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        manifest = json.loads(result.stdout)
        assert manifest["name"] == "inner"
        assert manifest["entry_point"] == "demo:Outer.Inner"

    def test_manifest_emits_clean_error_for_invalid_meta(self, tmp_path: Path):
        """A plugin with invalid meta must produce a clean error, not a traceback."""
        (tmp_path / "bad.py").write_text(
            textwrap.dedent(
                """
                from fastmcp.server.plugins import Plugin, PluginMeta

                class Bad(Plugin):
                    meta = PluginMeta(
                        name="bad",
                        version="0.1.0",
                        dependencies=["not a valid pep508 spec!!"],
                    )
                """
            )
        )
        result = _run_fastmcp("plugin", "manifest", "bad:Bad", cwd=tmp_path)
        assert result.returncode == 1
        # Error goes through logger.error, not as a Python traceback.
        assert "Traceback" not in result.stderr
        assert "PEP 508" in result.stderr

    def test_manifest_emits_clean_error_for_missing_module(self, tmp_path: Path):
        result = _run_fastmcp(
            "plugin", "manifest", "nonexistent_module:Thing", cwd=tmp_path
        )
        assert result.returncode == 1
        assert "Traceback" not in result.stderr
