"""Server protocol-version floor: declaration, negotiation-time enforcement, and
startup incoherence detection.

A FastMCP server may depend on features that only exist on a particular MCP
protocol era. The clearest example is the modern (``2026-07-28``) multi-round
"guard" pattern (SEP-2322): a tool that returns an ``InputRequiredResult`` works
only on a modern connection. A legacy client that reaches such a tool over the
initialize handshake gets a confusing era error *mid tool-call* instead of a
clear refusal at connect time.

This module lets a server declare a minimum protocol version (a "floor") and
enforces it at the two connection-negotiation points FastMCP owns:

* the initialize handshake (legacy era), refused before the handshake commits;
* ``server/discover`` (modern era), which always satisfies any currently
  declarable floor because the modern era is the newest era.

It also runs a conservative, warning-only coherence check at server startup: it
infers the modern requirement from registered guard tools and flags declared
floors that contradict a configured back-channel handler.

.. note::
    The public spelling (``FastMCP(min_protocol_version=...)``) is **provisional**
    and expected to change. The negotiation hook and the inference rules in this
    module are valid under any eventual spelling; only the constructor keyword is
    a placeholder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from mcp_types.version import (
    HANDSHAKE_PROTOCOL_VERSIONS,
    KNOWN_PROTOCOL_VERSIONS,
    LATEST_HANDSHAKE_VERSION,
    MODERN_PROTOCOL_VERSIONS,
    is_version_at_least,
)

from fastmcp.tools.function_parsing import _contains_input_required
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    import mcp_types

    from fastmcp.server.server import FastMCP
    from fastmcp.tools.base import Tool

logger = get_logger(__name__)

# The oldest modern (per-request-envelope) protocol version. A floor at or above
# this value means "modern connections only" — no handshake-era client can
# satisfy it, since the handshake era tops out at LATEST_HANDSHAKE_VERSION.
_MODERN_FLOOR = MODERN_PROTOCOL_VERSIONS[0]


def validate_protocol_floor(value: str | None) -> str | None:
    """Validate a declared protocol-version floor at construction time.

    Returns the value unchanged when it is ``None`` (no floor) or a known
    protocol revision. Raises ``ValueError`` for any unrecognized string — a
    floor the SDK could never negotiate is a programming error, not a runtime
    condition to warn about.
    """
    if value is None:
        return None
    if value not in KNOWN_PROTOCOL_VERSIONS:
        raise ValueError(
            f"min_protocol_version={value!r} is not a known MCP protocol version. "
            f"Known versions: {', '.join(KNOWN_PROTOCOL_VERSIONS)}."
        )
    return value


def handshake_negotiated_version(requested: str | None) -> str:
    """The version an initialize handshake would settle on for ``requested``.

    Mirrors the SDK's ``ServerRunner._negotiate_initialize``: a client's
    requested handshake revision is honored; anything else (an unknown string,
    or a modern-era version the handshake cannot serve) counters with the newest
    handshake revision. The connection operates at the returned version, so it is
    what the floor check must compare against.
    """
    if requested is not None and requested in HANDSHAKE_PROTOCOL_VERSIONS:
        return requested
    return LATEST_HANDSHAKE_VERSION


def enforce_handshake_floor(
    fastmcp: FastMCP,
    init_message: mcp_types.InitializeRequest | None,
) -> None:
    """Refuse an initialize handshake that cannot meet the server's floor.

    Called from the framework-owned initialize path (the SDK's middleware layer)
    before the handshake commits. When the connection's negotiated handshake
    version is below the floor, raises ``MCPError`` so the client sees a clear
    connect-time refusal naming the required version instead of a runtime era
    error later. A ``None`` floor (the default) never refuses.
    """
    floor = fastmcp.min_protocol_version
    if floor is None or init_message is None:
        return
    requested = init_message.params.protocol_version
    negotiated = handshake_negotiated_version(requested)
    if is_version_at_least(negotiated, floor):
        return
    detail = (
        "Connect using the modern protocol (server/discover) instead."
        if is_version_at_least(floor, _MODERN_FLOOR)
        else "Upgrade the client or connect with a newer protocol version."
    )
    raise MCPError(
        code=INVALID_PARAMS,
        message=(
            f"Server {fastmcp.name!r} requires MCP protocol version {floor} or "
            f"newer; the initialize handshake offered {requested!r} "
            f"(negotiates to {negotiated}). {detail}"
        ),
        data={"requiredProtocolVersion": floor, "offeredProtocolVersion": requested},
    )


def tool_requires_modern(tool: Tool) -> bool:
    """True when a tool's return annotation makes it a modern-only guard tool.

    A guard tool (SEP-2322) returns an ``InputRequiredResult`` to ask the client
    for input across rounds; that pattern exists only on the modern era. Detection
    reuses ``_contains_input_required`` over the tool's captured return annotation,
    so every union/alias/``Annotated`` shape the parser recognizes is covered. Only
    ``FunctionTool`` carries a return annotation; other tool kinds return ``False``
    (a conservative miss, not a false positive).
    """
    if not isinstance(tool, FunctionTool):
        return False
    return _contains_input_required(tool.return_type)


async def check_protocol_coherence(fastmcp: FastMCP) -> None:
    """Warn at startup when the declared floor and registered features conflict.

    Conservative by design: emits actionable warnings, never raises. Two rules:

    1. **Modern-only guard tools under a non-modern floor.** If any registered
       tool is a guard tool (returns ``InputRequiredResult``) but the floor does
       not guarantee a modern connection, handshake-era clients that reach those
       tools fail mid-call. Recommends declaring a modern floor.
    2. **Modern floor with a back-channel sampling fallback.** A modern floor
       forbids the server-initiated back-channel, so a ``sampling_handler`` set to
       ``"fallback"`` (prefer the client's model, fall back to the local handler)
       can never actually reach the client — the local handler always runs. Flags
       the dead preference.

    Runtime-only back-channel usage (``ctx.elicit`` / ``ctx.sample`` /
    ``ctx.list_roots``) has no reliable static signal, so it is deliberately not
    inferred here.
    """
    floor = fastmcp.min_protocol_version
    floor_is_modern = floor is not None and is_version_at_least(floor, _MODERN_FLOOR)

    # Inspect only this server's directly-registered tools. Aggregating mounted
    # children would route through their middleware chains (a startup side
    # effect); mounted or transformed guard tools are a conservative miss, not a
    # false positive. `LocalProvider.list_tools` is side-effect-free.
    try:
        tools = list(await fastmcp._local_provider.list_tools())
    except Exception as exc:
        logger.debug("Protocol coherence check could not list tools: %s", exc)
        tools = []

    if not floor_is_modern:
        guard_tools = sorted(t.name for t in tools if tool_requires_modern(t))
        if guard_tools:
            floor_desc = (
                "no minimum protocol version is declared"
                if floor is None
                else f"the declared floor is {floor}"
            )
            logger.warning(
                "Server %r registers guard tool(s) %s that return "
                "InputRequiredResult and require the modern MCP protocol "
                "(%s), but %s. Handshake-era clients calling these tools will "
                "fail mid-call. Declare min_protocol_version=%r to refuse such "
                "clients at connect time.",
                fastmcp.name,
                ", ".join(guard_tools),
                _MODERN_FLOOR,
                floor_desc,
                _MODERN_FLOOR,
            )

    if (
        floor_is_modern
        and fastmcp.sampling_handler is not None
        and fastmcp.sampling_handler_behavior == "fallback"
    ):
        logger.warning(
            "Server %r declares a modern protocol floor (%s) but configures a "
            "sampling_handler with behavior 'fallback'. The modern protocol "
            "forbids the server-initiated back-channel, so the fallback never "
            "reaches the client and the local handler always runs. Use "
            "sampling_handler_behavior='always' if that is intended, or lower "
            "the floor to allow the client back-channel.",
            fastmcp.name,
            floor,
        )
