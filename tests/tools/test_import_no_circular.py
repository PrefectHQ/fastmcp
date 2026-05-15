"""Regression test for #4147: circular import in fastmcp.tools.

Importing :mod:`fastmcp.tools` (or its submodules) must not trigger the
heavy ``fastmcp.server`` import chain. The chain previously re-entered
partially-initialized ``fastmcp.tools.function_tool`` and raised a
misleading ``ImportError`` claiming server support wasn't installed, on
fresh installs of fastmcp 3.3.0.
"""

from __future__ import annotations

import subprocess
import sys


def _run_import(stmt: str) -> subprocess.CompletedProcess[str]:
    """Run ``stmt`` in a fresh interpreter and return the completed process.

    A fresh subprocess is required because Python caches modules in
    ``sys.modules`` once an import succeeds; the regression only
    reproduces from a clean module cache.
    """
    return subprocess.run(
        [sys.executable, "-c", stmt],
        capture_output=True,
        text=True,
    )


def test_import_fastmcp_tools_does_not_circular() -> None:
    """``from fastmcp.tools import tool`` must not raise."""
    result = _run_import("from fastmcp.tools import tool")
    assert result.returncode == 0, (
        f"Import failed (#4147 regression):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_import_function_tool_does_not_circular() -> None:
    """``from fastmcp.tools.function_tool import FunctionTool`` must not raise."""
    result = _run_import("from fastmcp.tools.function_tool import FunctionTool")
    assert result.returncode == 0, (
        f"Import failed (#4147 regression):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_import_server_submodule_does_not_circular() -> None:
    """Importing a server submodule must not trigger the full server chain.

    ``from fastmcp.server.tasks.config import TaskConfig`` previously
    triggered ``fastmcp.server.__init__`` which eagerly imported the
    heavy ``Context``/``FastMCP`` chain.
    """
    result = _run_import("from fastmcp.server.tasks.config import TaskConfig")
    assert result.returncode == 0, (
        f"Import failed (#4147 regression):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
