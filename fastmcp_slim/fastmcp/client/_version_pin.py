"""Marks the tool call currently in flight as version-pinned.

A version-pinned call executes a specific version on the server, but the
session's output-schema cache is keyed by tool name and holds only the highest
version's schema (``list_tools`` deduplicates by identifier), and the executed
version's schema is not advertised on the wire. Result revalidation therefore
has no correct schema to use for that call, and only for that call.

The marker is a ContextVar rather than a session attribute so the decision stays
per call: concurrent calls on one session do not observe each other's pinning.
"""

from contextvars import ContextVar

version_pinned: ContextVar[bool] = ContextVar("fastmcp_version_pinned", default=False)
