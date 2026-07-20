"""Server protocol-version restriction: declaration, enforcement, and startup
coherence checks.

A server declares the *set* of protocol versions it serves. Membership — not
ordering — decides whether a connection is accepted, so a handshake-only server
refuses modern connections just as a modern-only server refuses handshake
connections. Enforcement is era-aware: FastMCP can only veto a connection, and
the SDK negotiates the handshake revision with no knowledge of the declaration,
so a handshake-version pin asserts the handshake *era*, not an exact revision. A
startup check warns (never raises) when a declared set cannot carry a capability
the server actually uses, and stays silent when nothing was declared.
"""

from __future__ import annotations

import logging

import mcp_types
import pytest
from exceptiongroup import BaseExceptionGroup
from mcp.client import Client as SDKClient
from mcp.server import Server as LowLevelServer
from mcp.shared.exceptions import MCPError
from mcp_types.version import (
    HANDSHAKE_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSIONS,
)

from fastmcp import Client, Context, FastMCP
from fastmcp.server.protocol_versions import (
    enforce_handshake_protocol_version,
    handshake_negotiated_version,
    tool_uses_multi_round_trip,
    validate_protocol_versions,
)
from fastmcp.tools.base import Tool

_COHERENCE_LOGGER = "fastmcp.server.protocol_versions"


def _server(mcp: FastMCP) -> LowLevelServer:
    """The lowlevel Server the SDK client connects to in-process."""
    return mcp._mcp_server


def _find_mcp_error(exc: BaseException) -> MCPError | None:
    """Unwrap the MCPError a refused in-memory connection surfaces.

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
    "declared, expected",
    [
        (None, None),
        (MODERN_PROTOCOL_VERSIONS, ("2026-07-28",)),
        (HANDSHAKE_PROTOCOL_VERSIONS, HANDSHAKE_PROTOCOL_VERSIONS),
        (["2026-07-28"], ("2026-07-28",)),
        (["2025-06-18", "2026-07-28"], ("2025-06-18", "2026-07-28")),
        # Normalized to SDK order, deduplicated.
        (["2026-07-28", "2024-11-05", "2026-07-28"], ("2024-11-05", "2026-07-28")),
        # Any iterable, not just a sequence.
        ({"2025-11-25"}, ("2025-11-25",)),
    ],
)
def test_valid_protocol_versions_normalized(declared, expected):
    assert validate_protocol_versions(declared) == expected
    assert FastMCP("s", protocol_versions=declared).protocol_versions == expected


@pytest.mark.parametrize(
    "declared",
    [["9999-01-01"], ["modern"], ["2026"], [""], ["2026-07-28", "handshake"]],
)
def test_unknown_protocol_version_rejected(declared):
    with pytest.raises(ValueError, match="unknown MCP protocol version"):
        validate_protocol_versions(declared)
    with pytest.raises(ValueError, match="unknown MCP protocol version"):
        FastMCP("s", protocol_versions=declared)


def test_empty_protocol_versions_rejected():
    with pytest.raises(ValueError, match="at least one protocol version"):
        validate_protocol_versions([])
    with pytest.raises(ValueError, match="at least one protocol version"):
        FastMCP("s", protocol_versions=[])


def test_default_serves_every_version():
    assert FastMCP("s").protocol_versions is None


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

    assert tool_uses_multi_round_trip(Tool.from_function(guard)) is True


def test_plain_tool_not_flagged():
    def plain(x: int) -> int:
        return x

    assert tool_uses_multi_round_trip(Tool.from_function(plain)) is False


# ---------------------------------------------------------------------------
# Enforcement: modern-only server refuses handshake clients
# ---------------------------------------------------------------------------


@pytest.fixture
def modern_only_server() -> FastMCP:
    mcp = FastMCP("modern-only", protocol_versions=MODERN_PROTOCOL_VERSIONS)

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    return mcp


async def test_modern_only_refuses_handshake(modern_only_server):
    with pytest.raises(BaseException) as excinfo:
        async with SDKClient(_server(modern_only_server), mode="legacy") as client:
            await client.list_tools()
    err = _find_mcp_error(excinfo.value)
    assert err is not None
    assert err.code == mcp_types.UNSUPPORTED_PROTOCOL_VERSION
    assert "2026-07-28" in err.message
    assert "server/discover" in err.message


@pytest.mark.parametrize("mode", ["auto", "2026-07-28"])
async def test_modern_only_allows_modern(modern_only_server, mode):
    async with SDKClient(_server(modern_only_server), mode=mode) as client:
        result = await client.list_tools()
    assert [t.name for t in result.tools] == ["add"]


# ---------------------------------------------------------------------------
# Enforcement: handshake-only server refuses modern clients
#
# This case is only expressible because the declaration is a set, not a bound:
# under a minimum-version model there was no way to say "the session era".
# ---------------------------------------------------------------------------


@pytest.fixture
def handshake_only_server() -> FastMCP:
    mcp = FastMCP("handshake-only", protocol_versions=HANDSHAKE_PROTOCOL_VERSIONS)

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    return mcp


async def test_handshake_only_allows_handshake(handshake_only_server):
    async with SDKClient(_server(handshake_only_server), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        result = await client.list_tools()
    assert [t.name for t in result.tools] == ["add"]


async def test_handshake_only_refuses_pinned_modern_client(handshake_only_server):
    """A client pinned to a modern version never probes discover, so the refusal
    has to land on the request itself."""
    with pytest.raises(BaseException) as excinfo:
        async with SDKClient(
            _server(handshake_only_server), mode="2026-07-28"
        ) as client:
            await client.list_tools()
    err = _find_mcp_error(excinfo.value)
    assert err is not None
    assert err.code == mcp_types.UNSUPPORTED_PROTOCOL_VERSION
    assert "initialize" in err.message


# ---------------------------------------------------------------------------
# Enforcement: pinned single version, and the unrestricted default
# ---------------------------------------------------------------------------


async def test_pinned_version_allows_exact_match():
    mcp = FastMCP("pinned", protocol_versions=["2025-11-25"])

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    async with SDKClient(_server(mcp), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        result = await client.list_tools()
    assert [t.name for t in result.tools] == ["add"]


def _initialize_request(version: str) -> mcp_types.InitializeRequest:
    return mcp_types.InitializeRequest.model_validate(
        {
            "method": "initialize",
            "params": {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
    )


@pytest.mark.parametrize("offered", ["2024-11-05", "2025-03-26", "2025-06-18"])
def test_pinned_version_accepts_other_handshake_revision(offered):
    """A handshake-version pin asserts the handshake *era*, not an exact revision.

    FastMCP can only veto the handshake, and the SDK negotiates the revision with
    no knowledge of the pin — so a server pinned to one handshake revision still
    accepts a client offering another handshake revision. The connection just
    settles on whatever the SDK negotiated, not on the pinned revision. (This
    replaces a test that asserted the opposite, which encoded the pre-fix bug:
    refusing an ordinary handshake client whenever it offered a handshake
    revision other than the pinned one.)
    """
    mcp = FastMCP("pinned", protocol_versions=["2025-11-25"])

    # No raise: the pinned revision and the offered revision are both handshake.
    enforce_handshake_protocol_version(mcp, _initialize_request(offered))


def test_pinned_version_accepts_matching_handshake():
    mcp = FastMCP("pinned", protocol_versions=["2025-11-25"])
    enforce_handshake_protocol_version(mcp, _initialize_request("2025-11-25"))


@pytest.mark.parametrize("offered", ["2025-11-25", "2025-06-18", "garbage", None])
def test_older_handshake_pin_accepts_any_handshake_offer_at_hook(offered):
    """The review-comment bug, at the enforcement hook.

    A server pinned to an older handshake revision must not refuse a client that
    offers a newer (or unknown, which the SDK counters to the newest) handshake
    revision. The SDK negotiates within the handshake era regardless of the pin,
    and FastMCP cannot counter-offer the pinned revision — only veto — so the
    honest behavior is to accept, since the server does serve the handshake era.
    """
    mcp = FastMCP("older-pin", protocol_versions=["2024-11-05"])

    # No raise: the server serves the handshake era, so the handshake is served.
    enforce_handshake_protocol_version(mcp, _initialize_request(offered or "garbage"))


async def test_older_handshake_pin_accepts_normal_client_end_to_end():
    """End-to-end review-comment regression: a server pinned to `2024-11-05`
    accepts an ordinary legacy client that requests `2025-11-25`.

    The pin declares the handshake era; the SDK negotiates the revision. The
    connection settles on `2025-11-25` (what the SDK negotiated), not the pinned
    `2024-11-05`, which is exactly why a handshake-revision pin is era-level: the
    server cannot force the client down to the pinned revision.
    """
    mcp = FastMCP("older-pin", protocol_versions=["2024-11-05"])

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    async with SDKClient(_server(mcp), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        result = await client.list_tools()
    assert [t.name for t in result.tools] == ["add"]


def test_unrestricted_server_never_refuses_handshake():
    mcp = FastMCP("open")
    enforce_handshake_protocol_version(mcp, _initialize_request("2024-11-05"))


async def test_fastmcp_client_default_reaches_handshake_only_server():
    """`fastmcp.Client` defaults to the handshake in memory, which a
    handshake-only server serves directly."""
    mcp = FastMCP("handshake-only", protocol_versions=HANDSHAKE_PROTOCOL_VERSIONS)

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    async with Client(mcp) as client:
        assert [t.name for t in await client.list_tools()] == ["add"]


async def test_fastmcp_client_needs_modern_mode_for_modern_only_server():
    """The mirror case: a modern-only server refuses the default in-memory
    handshake, and the refusal names the modern protocol."""
    mcp = FastMCP("modern-only", protocol_versions=MODERN_PROTOCOL_VERSIONS)

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    with pytest.raises(MCPError, match="server/discover"):
        async with Client(mcp) as client:
            await client.list_tools()

    async with Client(mcp, mode="auto") as client:
        assert [t.name for t in await client.list_tools()] == ["add"]


@pytest.mark.parametrize("mode", ["legacy", "auto", "2026-07-28"])
async def test_default_allows_every_era(mode):
    mcp = FastMCP("open")

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    async with SDKClient(_server(mcp), mode=mode) as client:
        result = await client.list_tools()
    assert [t.name for t in result.tools] == ["add"]


# ---------------------------------------------------------------------------
# Startup coherence check (warnings only, silent unless declared)
# ---------------------------------------------------------------------------


def _coherence_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == _COHERENCE_LOGGER and r.levelno == logging.WARNING
    ]


async def _warnings_from_startup(mcp: FastMCP, caplog) -> list[str]:
    with caplog.at_level(logging.WARNING, logger=_COHERENCE_LOGGER):
        async with mcp._lifespan_manager():
            pass
    return _coherence_warnings(caplog)


async def test_guard_tool_without_declaration_is_silent(caplog):
    """Declaring nothing asserts nothing, so there is no contradiction to warn
    about — the guard tool raises its own clear era error if reached."""
    mcp = FastMCP("guardy")

    @mcp.tool
    def ask(x: int) -> str | mcp_types.InputRequiredResult:
        return "ok"

    assert await _warnings_from_startup(mcp, caplog) == []


async def test_guard_tool_under_handshake_only_warns(caplog):
    mcp = FastMCP("guardy", protocol_versions=HANDSHAKE_PROTOCOL_VERSIONS)

    @mcp.tool
    def ask(x: int) -> str | mcp_types.InputRequiredResult:
        return "ok"

    warnings = await _warnings_from_startup(mcp, caplog)
    assert any("ask" in w and "InputRequiredResult" in w for w in warnings)


async def test_guard_tool_under_modern_declaration_silent(caplog):
    mcp = FastMCP("guardy", protocol_versions=MODERN_PROTOCOL_VERSIONS)

    @mcp.tool
    def ask(x: int) -> str | mcp_types.InputRequiredResult:
        return "ok"

    assert await _warnings_from_startup(mcp, caplog) == []


async def test_guard_tool_under_mixed_declaration_silent(caplog):
    """A declaration that still includes a modern version can carry guard tools."""
    mcp = FastMCP("guardy", protocol_versions=["2025-11-25", "2026-07-28"])

    @mcp.tool
    def ask(x: int) -> str | mcp_types.InputRequiredResult:
        return "ok"

    assert await _warnings_from_startup(mcp, caplog) == []


async def _sampling_handler(messages, params, context):
    return "x"


async def test_fallback_sampling_under_modern_declaration_warns(caplog):
    mcp = FastMCP(
        "samp",
        protocol_versions=MODERN_PROTOCOL_VERSIONS,
        sampling_handler=_sampling_handler,
        sampling_handler_behavior="fallback",
    )

    warnings = await _warnings_from_startup(mcp, caplog)
    assert any("back-channel" in w and "fallback" in w for w in warnings)


async def test_fallback_sampling_without_declaration_is_silent(caplog):
    mcp = FastMCP(
        "samp",
        sampling_handler=_sampling_handler,
        sampling_handler_behavior="fallback",
    )

    assert await _warnings_from_startup(mcp, caplog) == []


async def test_always_sampling_under_modern_declaration_silent(caplog):
    mcp = FastMCP(
        "samp",
        protocol_versions=MODERN_PROTOCOL_VERSIONS,
        sampling_handler=_sampling_handler,
        sampling_handler_behavior="always",
    )

    assert await _warnings_from_startup(mcp, caplog) == []


async def test_fallback_sampling_under_handshake_only_silent(caplog):
    mcp = FastMCP(
        "samp",
        protocol_versions=HANDSHAKE_PROTOCOL_VERSIONS,
        sampling_handler=_sampling_handler,
        sampling_handler_behavior="fallback",
    )

    assert await _warnings_from_startup(mcp, caplog) == []


async def test_plain_server_is_coherent(caplog):
    mcp = FastMCP("clean", protocol_versions=MODERN_PROTOCOL_VERSIONS)

    @mcp.tool
    def plain(a: int) -> int:
        return a

    assert await _warnings_from_startup(mcp, caplog) == []


async def test_guard_tool_reaches_modern_client():
    """A guard tool served under a modern declaration works end-to-end."""
    mcp = FastMCP("guarded", protocol_versions=MODERN_PROTOCOL_VERSIONS)

    @mcp.tool
    async def confirm(ctx: Context) -> str | mcp_types.InputRequiredResult:
        return "confirmed"

    async with SDKClient(_server(mcp), mode="auto") as client:
        result = await client.call_tool("confirm", {})
    assert result.is_error is False
