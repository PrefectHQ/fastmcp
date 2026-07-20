"""Server protocol-version restriction: declaration, enforcement, and startup
coherence checks.

A FastMCP server may depend on features that exist on only one MCP protocol era.
The modern (``2026-07-28``) era added the multi-round-trip "guard" pattern
(SEP-2322) and *removed* the server-initiated back-channel (``ctx.elicit``,
``ctx.sample``, ``ctx.list_roots``) that the handshake eras provide. Neither era
is a superset of the other, so a server can legitimately require either one.

Protocol versions are therefore modeled the way the SDK models them — as *an
enumerated set, not an ordered scalar* (see ``mcp_types.version``). A server
declares the set of protocol versions it is willing to serve, and enforcement is
set membership:

    from mcp_types.version import MODERN_PROTOCOL_VERSIONS

    FastMCP("guarded", protocol_versions=MODERN_PROTOCOL_VERSIONS)

The SDK's era tuples are the durable way to name an era: they grow on their own
when the SDK adds a revision to an era, so a server pinned to
``MODERN_PROTOCOL_VERSIONS`` keeps working across SDK upgrades without FastMCP
inventing alias vocabulary of its own.

Enforcement happens at both connection paths FastMCP owns:

* the initialize handshake, refused before the handshake commits;
* every modern per-request envelope, refused on the request itself. A client
  pinned to a modern version never probes ``server/discover``, so refusing
  discovery alone would not cover it — the request *is* the connection.

Refusals use the spec-standard ``-32022`` unsupported-protocol-version error
carrying the server's supported list, so a negotiating (``mode="auto"``) client
reads it as guidance rather than as a dead end: refused at ``server/discover``
by a handshake-only server, it sees handshake versions in ``supported`` and
falls back to the initialize handshake on its own.

FastMCP can only *veto* a connection, never steer the version the peer settles
on, and the two eras negotiate their version differently — so enforcement is
era-aware. A modern connection pins an exact version in every per-request
envelope, so a modern-version declaration enforces exact membership. The
handshake era is negotiated by the SDK's initialize handler
(``ServerRunner._negotiate_initialize``), which honors the client's requested
revision (or counters with the newest handshake revision) with no knowledge of
this declaration; FastMCP cannot make it counter-offer a specific revision. A
handshake-version declaration therefore enforces *era* membership, not an exact
revision: a server that declares any handshake version serves the handshake era
and accepts the handshake, running at whatever revision the SDK negotiates, and
only a server that declares no handshake version refuses it. Pinning a single
handshake revision (``["2025-06-18"]``) narrows nothing the SDK will honor — the
connection still settles on whatever revision the client and SDK negotiate.

Declaring nothing (the default) serves every era the SDK supports.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mcp.shared.exceptions import MCPError
from mcp_types import (
    UNSUPPORTED_PROTOCOL_VERSION,
    UnsupportedProtocolVersionErrorData,
)
from mcp_types.version import (
    HANDSHAKE_PROTOCOL_VERSIONS,
    KNOWN_PROTOCOL_VERSIONS,
    LATEST_HANDSHAKE_VERSION,
    MODERN_PROTOCOL_VERSIONS,
)

from fastmcp.tools.function_parsing import _contains_input_required
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    import mcp_types

    from fastmcp.server.server import FastMCP
    from fastmcp.tools.base import Tool

logger = get_logger(__name__)


def validate_protocol_versions(
    value: Iterable[str] | None,
) -> tuple[str, ...] | None:
    """Validate and normalize a declared set of protocol versions.

    Returns ``None`` unchanged (no restriction), or the declared versions as a
    deduplicated tuple in SDK order. Raises ``ValueError`` for an empty set (a
    server that serves no protocol version can never be reached) or for any
    string the SDK does not recognize — a version the SDK could never negotiate
    is a programming error, not a runtime condition to warn about.
    """
    if value is None:
        return None
    declared = list(value)
    unknown = [v for v in declared if v not in KNOWN_PROTOCOL_VERSIONS]
    if unknown:
        raise ValueError(
            f"protocol_versions contains unknown MCP protocol version(s): "
            f"{', '.join(repr(v) for v in unknown)}. "
            f"Known versions: {', '.join(KNOWN_PROTOCOL_VERSIONS)}. "
            f"Prefer the SDK's era tuples "
            f"(mcp_types.version.MODERN_PROTOCOL_VERSIONS / "
            f"HANDSHAKE_PROTOCOL_VERSIONS) over literal strings."
        )
    normalized = tuple(v for v in KNOWN_PROTOCOL_VERSIONS if v in declared)
    if not normalized:
        raise ValueError(
            "protocol_versions must name at least one protocol version. "
            "Pass None (the default) to serve every protocol version."
        )
    return normalized


def describe_protocol_versions(versions: Sequence[str]) -> str:
    """Human-readable description of a declared version set for error messages."""
    if tuple(versions) == MODERN_PROTOCOL_VERSIONS:
        return (
            f"the modern protocol ({', '.join(versions)}), reached via server/discover"
        )
    if tuple(versions) == HANDSHAKE_PROTOCOL_VERSIONS:
        return f"the handshake protocol ({', '.join(versions)}), reached via initialize"
    return ", ".join(versions)


def _remedy_for(versions: Sequence[str]) -> str:
    """Actionable next step for a client that failed the version check."""
    wants_modern = any(v in MODERN_PROTOCOL_VERSIONS for v in versions)
    wants_handshake = any(v in HANDSHAKE_PROTOCOL_VERSIONS for v in versions)
    if wants_modern and not wants_handshake:
        return "Connect using the modern protocol (server/discover) instead."
    if wants_handshake and not wants_modern:
        return "Connect using the initialize handshake instead."
    return "Use one of the protocol versions this server serves."


def _serves_version(allowed: Sequence[str], version: str) -> bool:
    """Whether a server declaring ``allowed`` serves a connection at ``version``.

    Enforcement is era-aware because the two eras negotiate their version
    differently and FastMCP can only *veto* a connection — never steer the
    version the peer settles on:

    * A modern version rides a per-request envelope that pins an exact version,
      so membership is exact: the server serves it only when ``version`` is in
      ``allowed``.
    * A handshake version is negotiated by the SDK's initialize handler, which
      honors the client's requested revision (or counters with the newest
      handshake revision) with no knowledge of ``allowed``. FastMCP cannot make
      the SDK counter-offer a specific revision, so a handshake-version pin
      asserts *era* membership only: the server serves the handshake connection
      when it declared any handshake version, whatever revision the SDK settled
      on.
    """
    if version in HANDSHAKE_PROTOCOL_VERSIONS:
        return not set(allowed).isdisjoint(HANDSHAKE_PROTOCOL_VERSIONS)
    return version in allowed


def protocol_version_error(fastmcp: FastMCP, version: str) -> MCPError | None:
    """The refusal for ``version``, or ``None`` when the server serves it.

    A server that declared nothing (the default) serves every version and never
    refuses. Otherwise the decision is era-aware set membership (see
    ``_serves_version``): a modern version must be an exact member, while a
    handshake version is served whenever the declaration includes any handshake
    version, because the SDK negotiates the handshake revision and FastMCP can
    only veto — not steer — the version the connection settles on. A
    handshake-only server still refuses modern connections just as a modern-only
    server refuses handshake connections; only within-handshake revision pinning
    is unenforceable.

    The refusal is the spec-standard ``-32022`` unsupported-protocol-version
    error carrying the server's supported list, which is what a negotiating
    client already knows how to read: an ``auto`` client refused at
    ``server/discover`` sees handshake versions in ``supported`` and falls back
    to the initialize handshake instead of failing the connect.
    """
    allowed = fastmcp.protocol_versions
    if allowed is None or _serves_version(allowed, version):
        return None
    return MCPError(
        code=UNSUPPORTED_PROTOCOL_VERSION,
        message=(
            f"Server {fastmcp.name!r} serves {describe_protocol_versions(allowed)}; "
            f"this connection uses MCP protocol version {version}. "
            f"{_remedy_for(allowed)}"
        ),
        data=UnsupportedProtocolVersionErrorData(
            supported=list(allowed), requested=version
        ).model_dump(by_alias=True, mode="json"),
    )


def handshake_negotiated_version(requested: str | None) -> str:
    """The version an initialize handshake would settle on for ``requested``.

    Mirrors the SDK's ``ServerRunner._negotiate_initialize``: a client's
    requested handshake revision is honored; anything else (an unknown string,
    or a modern-era version the handshake cannot serve) counters with the newest
    handshake revision. The connection operates at the returned version, so it is
    the honest value to report as ``requested`` when a modern-only server refuses
    the handshake — the enforcement decision itself is era-aware (see
    ``_serves_version``) and does not turn on this exact revision.
    """
    if requested is not None and requested in HANDSHAKE_PROTOCOL_VERSIONS:
        return requested
    return LATEST_HANDSHAKE_VERSION


def enforce_handshake_protocol_version(
    fastmcp: FastMCP,
    init_message: mcp_types.InitializeRequest | None,
) -> None:
    """Refuse an initialize handshake the server does not serve.

    Called from the framework-owned initialize path before the handshake
    commits, so the client sees a clear connect-time refusal naming what the
    server serves instead of a confusing era error mid tool-call.

    Enforcement is era-level (see ``_serves_version``): a server that declares
    any handshake version serves the handshake era and accepts the handshake,
    even when the client offers a different handshake revision than the one
    pinned — the SDK negotiates the revision and FastMCP cannot steer it, only
    veto. The refusal fires only for a genuine cross-era mismatch: a modern-only
    server has no handshake version to share, so it refuses the handshake and
    names the modern versions it does serve.
    """
    if fastmcp.protocol_versions is None or init_message is None:
        return
    requested = init_message.params.protocol_version
    error = protocol_version_error(fastmcp, handshake_negotiated_version(requested))
    if error is not None:
        raise error


# ---------------------------------------------------------------------------
# Capability map
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtocolCapability:
    """A server-side capability that only some protocol versions can carry.

    ``detect`` returns evidence (tool names, a configuration description) that
    the server actually uses the capability, and is consulted only when the
    declared version set cannot carry it — so detection never costs anything on
    a server that declared nothing.
    """

    key: str
    label: str
    versions: tuple[str, ...]
    remedy: str
    detect: Callable[[FastMCP, Sequence[Tool]], list[str]]


def tool_uses_multi_round_trip(tool: Tool) -> bool:
    """True when a tool's return annotation makes it a modern-only guard tool.

    A guard tool (SEP-2322) returns an ``InputRequiredResult`` to ask the client
    for input across rounds; that pattern exists only on the modern era.
    Detection reuses ``_contains_input_required`` over the tool's captured return
    annotation, so every union/alias/``Annotated`` shape the parser recognizes is
    covered. Only ``FunctionTool`` carries a return annotation; other tool kinds
    return ``False`` (a conservative miss, not a false positive).
    """
    if not isinstance(tool, FunctionTool):
        return False
    return _contains_input_required(tool.return_type)


def _detect_multi_round_trip(fastmcp: FastMCP, tools: Sequence[Tool]) -> list[str]:
    return sorted(t.name for t in tools if tool_uses_multi_round_trip(t))


def _detect_client_back_channel(fastmcp: FastMCP, tools: Sequence[Tool]) -> list[str]:
    """Configuration-level evidence that the server wants the client back-channel.

    ``ctx.elicit`` / ``ctx.sample`` / ``ctx.list_roots`` are *runtime* calls with
    no reliable static signal, so only configuration contradictions are
    detectable. A ``sampling_handler`` set to ``"fallback"`` says "prefer the
    client's model, fall back to mine" — a preference that cannot be honored on a
    protocol version without the back-channel.
    """
    if (
        fastmcp.sampling_handler is not None
        and fastmcp.sampling_handler_behavior == "fallback"
    ):
        return ["sampling_handler with behavior='fallback'"]
    return []


PROTOCOL_CAPABILITIES: tuple[ProtocolCapability, ...] = (
    ProtocolCapability(
        key="multi_round_trip",
        label="multi-round-trip guard tools (they return InputRequiredResult)",
        versions=MODERN_PROTOCOL_VERSIONS,
        remedy=(
            "Include the modern versions in protocol_versions "
            "(mcp_types.version.MODERN_PROTOCOL_VERSIONS), or stop returning "
            "InputRequiredResult from these tools."
        ),
        detect=_detect_multi_round_trip,
    ),
    ProtocolCapability(
        key="client_back_channel",
        label=(
            "the server-initiated client back-channel "
            "(ctx.elicit / ctx.sample / ctx.list_roots)"
        ),
        versions=HANDSHAKE_PROTOCOL_VERSIONS,
        remedy=(
            "Include the handshake versions in protocol_versions "
            "(mcp_types.version.HANDSHAKE_PROTOCOL_VERSIONS), or use "
            "sampling_handler_behavior='always' so the local handler is the "
            "intended path."
        ),
        detect=_detect_client_back_channel,
    ),
    # Extension point: a capability that only some protocol versions can carry
    # is one entry here, not a new special case in the check below. The 2026
    # tasks extension is the next expected entry — when FastMCP implements it,
    # add a ProtocolCapability naming the versions that carry it and a `detect`
    # that reports the registered task-enabled components.
)


async def check_protocol_coherence(fastmcp: FastMCP) -> None:
    """Warn at startup when a declared version set contradicts what is registered.

    Warning-only by design, and **silent unless the server declared a version
    set**. A server with an ordinary mix of tools is fine: a guard tool raises a
    clear era error if an old client reaches for it, and warning about that at
    startup would train people to ignore warnings. The check fires only when the
    author asserted something and the registration contradicts the assertion.
    """
    allowed = fastmcp.protocol_versions
    if allowed is None:
        return

    unmet = [c for c in PROTOCOL_CAPABILITIES if set(allowed).isdisjoint(c.versions)]
    if not unmet:
        return

    # Inspect only this server's directly-registered tools. Aggregating mounted
    # children would route through their middleware chains (a startup side
    # effect); mounted or transformed components are a conservative miss, not a
    # false positive. `LocalProvider.list_tools` is side-effect-free.
    try:
        tools: list[Tool] = list(await fastmcp._local_provider.list_tools())
    except (LookupError, RuntimeError, ValueError) as exc:
        logger.debug("Protocol coherence check could not list tools: %s", exc)
        tools = []

    for capability in unmet:
        evidence = capability.detect(fastmcp, tools)
        if not evidence:
            continue
        logger.warning(
            "Server %r declares protocol_versions=%s, which cannot carry %s, "
            "but the server uses it: %s. %s",
            fastmcp.name,
            list(allowed),
            capability.label,
            ", ".join(evidence),
            capability.remedy,
        )
