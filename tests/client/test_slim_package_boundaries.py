from __future__ import annotations

import builtins
import contextlib
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
