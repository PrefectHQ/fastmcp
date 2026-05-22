from __future__ import annotations

import builtins
import contextlib
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@contextlib.contextmanager
def block_server_imports():
    original_import = builtins.__import__

    def blocked_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> types.ModuleType:
        if level == 0 and (
            name == "fastmcp.server" or name.startswith("fastmcp.server.")
        ):
            raise ImportError(f"blocked server import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    cast(Any, builtins).__import__ = blocked_import
    try:
        yield
    finally:
        cast(Any, builtins).__import__ = original_import


def test_client_http_headers_do_not_require_server() -> None:
    from fastmcp.client.dependencies import get_http_headers

    with block_server_imports():
        assert get_http_headers(include_all=True) == {}


async def test_multiserver_config_requires_server_for_now() -> None:
    from fastmcp.client.transports import MCPConfigTransport

    with block_server_imports():
        transport = MCPConfigTransport(
            {
                "mcpServers": {
                    "one": {"command": "uvx", "args": ["one"]},
                    "two": {"command": "uvx", "args": ["two"]},
                }
            }
        )

        with pytest.raises(
            ImportError, match="multiple servers require the full `fastmcp`"
        ):
            async with transport.connect_session():
                pass


def test_fastmcp_metapackage_delegates_to_slim() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()

    assert 'dynamic = ["version", "dependencies", "optional-dependencies"]' in pyproject
    assert "[project.scripts]" in pyproject
    assert 'fastmcp = "fastmcp.cli:app"' in pyproject
    assert "[tool.hatch.build.targets.wheel]" in pyproject
    assert "bypass-selection = true" in pyproject
    assert "only-include = []" in pyproject
    assert 'exclude = ["/*"]' in pyproject
    assert '"fastmcp-slim[client,server]=={{ version }}"' in pyproject


def test_fastmcp_slim_installs_private_import_root() -> None:
    pyproject = (PROJECT_ROOT / "fastmcp_slim" / "pyproject.toml").read_text()

    assert "[project.scripts]" not in pyproject
    assert 'fastmcp = "fastmcp.cli:app"' not in pyproject
    assert "[tool.hatch.build.targets.wheel]" in pyproject
    assert "bypass-selection = true" in pyproject
    assert "only-include = []" in pyproject
    assert "[tool.hatch.build.targets.wheel.force-include]" in pyproject
    assert 'fastmcp = "fastmcp_slim/fastmcp"' in pyproject
    assert '"fastmcp_slim.pth" = "fastmcp_slim.pth"' in pyproject

    pth_file = PROJECT_ROOT / "fastmcp_slim" / "fastmcp_slim.pth"
    assert pth_file.read_text().strip() == "fastmcp_slim"
