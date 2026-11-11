"""Marks the tool call currently in flight as version-pinned.

A version-pinned call executes a specific version on the server, but the
session's output-schema cache is keyed by tool name and holds only the schema
of the version ``tools/list`` advertised (``list_tools`` deduplicates by
identifier and lists the highest version). When the pinned version is a
different one, its schema is not advertised on the wire, so result
revalidation has no correct schema to use for that call, and only for that
call.

The marker holds the session that made the call rather than a bare flag: with
an in-memory transport the server handler runs in the caller's context, so a
bare flag would also exempt an unrelated ``Client`` call made from inside that
handler.
"""

from contextvars import ContextVar
from typing import NamedTuple


class PinnedCall(NamedTuple):
    """The session that made a pinned tool call, and the version it pinned."""

    session: object
    version: str


pinned_call: ContextVar[PinnedCall | None] = ContextVar(
    "fastmcp_pinned_call", default=None
)
