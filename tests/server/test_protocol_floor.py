"""Server protocol-version floor: declaration, negotiation-time enforcement, and
startup incoherence detection.

A server may declare a minimum protocol version. The initialize handshake refuses
clients below the floor before the handshake commits; the modern
(``server/discover``) era always satisfies any currently declarable floor. A
conservative startup check warns when the declared floor and the registered
features are incoherent.

The ``min_protocol_version`` spelling is provisional; these tests exercise the
mechanics, which hold under any eventual spelling.
"""

from __future__ import annotations

import logging

import mcp_types
import pytest
from exceptiongroup import BaseExceptionGroup
from mcp.client import Client as SDKClient
from mcp.server import Server as LowLevelServer
from mcp.shared.exceptions import MCPError

from fastmcp import Context, FastMCP
from fastmcp.server.protocol_floor import (
    handshake_negotiated_version,
    tool_requires_modern,
    validate_protocol_floor,
)
from fastmcp.tools.base import Tool

_COHERENCE_LOGGER = "fastmcp.server.protocol_floor"


def _server(mcp: FastMCP) -> LowLevelServer:
    """The lowlevel Server the SDK client connects to in-process."""
    return mcp._mcp_server


def _find_mcp_error(exc: BaseException) -> MCPError | None:
    """Unwrap the MCPError a refused in-memory handshake surfaces.

    The legacy in-memory transport runs ``initialize`` inside a task group, so a
    connect-time refusal propagates as an ``ExceptionGroup`` wrapping the
    ``MCPError`` rather than the bare error.
    """
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            found = _find_mcp_error(inner)
            if found is not None:
                return found
    if exc.__cause__ is not None:
        return _find_mcp_error(exc.__cause__)
    return None


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version",
    ["2024-11-05", "2025-06-18", "2025-11-25", "2026-07-28", None],
)
def test_valid_floor_accepted(version):
    assert validate_protocol_floor(version) == version
    assert FastMCP("s", min_protocol_version=version).min_protocol_version == version


@pytest.mark.parametrize("version", ["9999-01-01", "latest", "2026", ""])
def test_unknown_floor_rejected(version):
    with pytest.raises(ValueError, match="not a known MCP protocol version"):
        validate_protocol_floor(version)
    with pytest.raises(ValueError, match="not a known MCP protocol version"):
        FastMCP("s", min_protocol_version=version)


def test_default_is_no_floor():
    assert FastMCP("s").min_protocol_version is None


# ---------------------------------------------------------------------------
# Handshake negotiation mirror
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested, expected",
    [
        ("2025-11-25", "2025-11-25"),
        ("2025-06-18", "2025-06-18"),
        ("2024-11-05", "2024-11-05"),
        # A modern-era or unknown request counters with the newest handshake.
        ("2026-07-28", "2025-11-25"),
        ("garbage", "2025-11-25"),
        (None, "2025-11-25"),
    ],
)
def test_handshake_negotiated_version(requested, expected):
    assert handshake_negotiated_version(requested) == expected


# ---------------------------------------------------------------------------
# Guard-tool detection
# ---------------------------------------------------------------------------


def test_guard_tool_detected():
    def guard(x: int) -> str | mcp_types.InputRequiredResult:
        return "ok"

    assert tool_requires_modern(Tool.from_function(guard)) is True


def test_plain_tool_not_flagged():
    def plain(x: int) -> int:
        return x

    assert tool_requires_modern(Tool.from_function(plain)) is False


# ---------------------------------------------------------------------------
# Negotiation-time enforcement (handshake path)
# ---------------------------------------------------------------------------


@pytest.fixture
def floored_server() -> FastMCP:
    mcp = FastMCP("floored", min_protocol_version="2026-07-28")

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    return mcp


async def test_modern_floor_refuses_legacy_handshake(floored_server):
    with pytest.raises(BaseException) as excinfo:
        async with SDKClient(_server(floored_server), mode="legacy") as client:
            await client.list_tools()
    err = _find_mcp_error(excinfo.value)
    assert err is not None
    assert err.code == mcp_types.INVALID_PARAMS
    assert "2026-07-28" in err.message
    assert "server/discover" in err.message


@pytest.mark.parametrize("mode", ["auto", "2026-07-28"])
async def test_modern_floor_allows_modern(floored_server, mode):
    async with SDKClient(_server(floored_server), mode=mode) as client:
        result = await client.list_tools()
    assert [t.name for t in result.tools] == ["add"]


async def test_no_floor_allows_legacy_handshake():
    mcp = FastMCP("open")

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    async with SDKClient(_server(mcp), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        result = await client.list_tools()
    assert [t.name for t in result.tools] == ["add"]


async def test_handshake_floor_allows_equal_version_client():
    mcp = FastMCP("hs", min_protocol_version="2025-11-25")

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    async with SDKClient(_server(mcp), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        result = await client.list_tools()
    assert [t.name for t in result.tools] == ["add"]


# ---------------------------------------------------------------------------
# Startup incoherence detection (warnings only)
# ---------------------------------------------------------------------------


def _coherence_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == _COHERENCE_LOGGER and r.levelno == logging.WARNING
    ]


async def test_guard_tool_without_floor_warns(caplog):
    mcp = FastMCP("guardy")

    @mcp.tool
    def ask(x: int) -> str | mcp_types.InputRequiredResult:
        return "ok"

    with caplog.at_level(logging.WARNING, logger=_COHERENCE_LOGGER):
        async with mcp._lifespan_manager():
            pass

    warnings = _coherence_warnings(caplog)
    assert any("ask" in w and "modern" in w.lower() for w in warnings)


async def test_guard_tool_with_modern_floor_silent(caplog):
    mcp = FastMCP("guardy", min_protocol_version="2026-07-28")

    @mcp.tool
    def ask(x: int) -> str | mcp_types.InputRequiredResult:
        return "ok"

    with caplog.at_level(logging.WARNING, logger=_COHERENCE_LOGGER):
        async with mcp._lifespan_manager():
            pass

    assert _coherence_warnings(caplog) == []


async def test_modern_floor_with_fallback_sampling_warns(caplog):
    async def handler(messages, params, context):
        return "x"

    mcp = FastMCP(
        "samp",
        min_protocol_version="2026-07-28",
        sampling_handler=handler,
        sampling_handler_behavior="fallback",
    )

    with caplog.at_level(logging.WARNING, logger=_COHERENCE_LOGGER):
        async with mcp._lifespan_manager():
            pass

    warnings = _coherence_warnings(caplog)
    assert any("fallback" in w and "back-channel" in w for w in warnings)


async def test_modern_floor_with_always_sampling_silent(caplog):
    async def handler(messages, params, context):
        return "x"

    mcp = FastMCP(
        "samp",
        min_protocol_version="2026-07-28",
        sampling_handler=handler,
        sampling_handler_behavior="always",
    )

    with caplog.at_level(logging.WARNING, logger=_COHERENCE_LOGGER):
        async with mcp._lifespan_manager():
            pass

    assert _coherence_warnings(caplog) == []


async def test_plain_server_is_coherent(caplog):
    mcp = FastMCP("clean")

    @mcp.tool
    def plain(a: int) -> int:
        return a

    with caplog.at_level(logging.WARNING, logger=_COHERENCE_LOGGER):
        async with mcp._lifespan_manager():
            pass

    assert _coherence_warnings(caplog) == []


async def test_guard_tool_reaches_modern_client(floored_server):
    """A guard tool served under a modern floor works end-to-end on modern."""
    mcp = FastMCP("guarded", min_protocol_version="2026-07-28")

    @mcp.tool
    async def confirm(ctx: Context) -> str | mcp_types.InputRequiredResult:
        return "confirmed"

    async with SDKClient(_server(mcp), mode="auto") as client:
        result = await client.call_tool("confirm", {})
    assert result.is_error is False
