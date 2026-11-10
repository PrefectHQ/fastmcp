"""Fresh-interpreter import guards for the default HTTP server path."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


@pytest.mark.subprocess_heavy
def test_root_import_does_not_load_mcp_sdk() -> None:
    script = textwrap.dedent(
        """
        import sys

        import fastmcp

        assert fastmcp.settings is not None
        assert "mcp" not in sys.modules
        assert "fastmcp.exceptions" not in sys.modules
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.subprocess_heavy
def test_server_import_does_not_load_cli() -> None:
    script = textwrap.dedent(
        """
        import sys

        from fastmcp import FastMCP

        assert FastMCP is not None
        assert "fastmcp.utilities.cli" not in sys.modules
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.subprocess_heavy
def test_default_http_app_does_not_load_opt_in_integrations() -> None:
    script = textwrap.dedent(
        """
        import sys

        from fastmcp import FastMCP

        server = FastMCP("HTTP import guard")

        @server.tool
        def echo(value: str) -> str:
            return value

        app = server.http_app(transport="http", stateless_http=True)
        assert app is not None

        forbidden = (
            "fastmcp.server.event_store",
            "griffe",
            "jsonref",
            "key_value",
            "prefab_ui",
        )
        loaded = [
            name
            for name in sys.modules
            if any(name == root or name.startswith(f"{root}.") for root in forbidden)
        ]
        assert not loaded, loaded
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.subprocess_heavy
def test_fastmcp_server_import_does_not_load_context() -> None:
    script = textwrap.dedent(
        """
        import sys

        from fastmcp import FastMCP

        assert FastMCP is not None
        assert "fastmcp.server.context" not in sys.modules
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
