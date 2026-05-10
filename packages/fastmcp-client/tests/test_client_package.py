from __future__ import annotations

import builtins
import contextlib
import types
from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest


@contextlib.contextmanager
def block_full_fastmcp_imports():
    original_import = builtins.__import__

    def blocked_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> types.ModuleType:
        if level == 0 and (name == "fastmcp" or name.startswith("fastmcp.")):
            raise ImportError(f"blocked full fastmcp import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    cast(Any, builtins).__import__ = blocked_import
    try:
        yield
    finally:
        cast(Any, builtins).__import__ = original_import


def test_fastmcp_client_imports_without_full_fastmcp() -> None:
    with block_full_fastmcp_imports():
        from fastmcp_client import Client
        from fastmcp_client.client.transports import (
            ClientTransport,
            StdioTransport,
            StreamableHttpTransport,
        )
        from fastmcp_client.exceptions import ToolError
        from fastmcp_client.mcp_config import MCPConfig

        assert Client is not None
        assert ClientTransport is not None
        assert StdioTransport(command="uvx", args=["mcp-run-python", "stdio"])
        assert StreamableHttpTransport is not None
        assert ToolError is not None
        assert MCPConfig.from_dict(
            {"mcpServers": {"demo": {"command": "uvx", "args": ["demo"]}}}
        )


@pytest.mark.asyncio
async def test_multiserver_config_requires_full_fastmcp_for_now() -> None:
    from fastmcp_client.client.transports import MCPConfigTransport

    with block_full_fastmcp_imports():
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
