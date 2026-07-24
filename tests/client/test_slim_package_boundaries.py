from __future__ import annotations

import builtins
import contextlib
import subprocess
import sys
import textwrap
import types
from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest


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


@pytest.mark.asyncio
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


@pytest.mark.subprocess_heavy
def test_bare_slim_import_needs_only_mcp_types() -> None:
    """A bare `fastmcp-slim` install ships `mcp-types` but not the full `mcp` SDK.

    `mcp-types` is a core dependency (it only pulls pydantic + typing-extensions),
    while the full `mcp` package lives in the `[mcp]` extra pulled by
    `[client]`/`[server]`. With `mcp` absent but `mcp-types` present, `import
    fastmcp`, `import fastmcp.settings`, and `import fastmcp.types` must all
    succeed, while `fastmcp.FastMCP` and `fastmcp.Client` raise the friendly
    install-hint ImportError (they need `mcp.*` from the server/client extras).
    """
    script = textwrap.dedent(
        """
        import sys
        import importlib.abc

        class BlockFullMcp(importlib.abc.MetaPathFinder):
            # Block the full `mcp` package but leave `mcp_types` importable,
            # exactly as a bare `fastmcp-slim` install would present.
            def find_spec(self, name, path, target=None):
                if name == "mcp" or name.startswith("mcp."):
                    raise ImportError(f"blocked: {name}")
                return None

        sys.meta_path.insert(0, BlockFullMcp())

        import fastmcp
        import fastmcp.settings
        import fastmcp.types

        assert "mcp_types" in sys.modules, "mcp_types should load on a bare install"
        assert "mcp" not in sys.modules, "full mcp must not load on a bare install"

        for attr in ("FastMCP", "Client"):
            try:
                getattr(fastmcp, attr)
            except ImportError:
                pass
            else:
                raise AssertionError(f"fastmcp.{attr} should have raised ImportError")

        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("OK")
