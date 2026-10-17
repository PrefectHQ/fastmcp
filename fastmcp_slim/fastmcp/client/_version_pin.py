"""Marks the tool call currently in flight as version-pinned.

A version-pinned call executes a specific version on the server, but the
session's output-schema cache is keyed by tool name and holds only the highest
version's schema (``list_tools`` deduplicates by identifier), and the executed
version's schema is not advertised on the wire. Result revalidation therefore
has no correct schema to use for that call, and only for that call.

The marker holds the session that made the call rather than a bare flag: with
an in-memory transport the server handler runs in the caller's context, so a
bare flag would also exempt an unrelated ``Client`` call made from inside that
handler.
"""

from contextvars import ContextVar

version_pinned: ContextVar[object | None] = ContextVar(
    "fastmcp_version_pinned", default=None
)
