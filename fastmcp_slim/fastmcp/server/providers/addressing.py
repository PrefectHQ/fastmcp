"""Deterministic tool hashing for backend-tool routing and per-tool resources.

Each FastMCPApp backend tool gets a deterministic hash computed from its
app name + tool name. The hash serves two purposes:

1. **Backend-tool routing.** Tools with ``"app"`` in their visibility are
   callable via ``<hash>_<local_name>``. The dispatcher parses the prefix,
   then walks providers recursively (same pattern as the old ``get_app_tool``)
   to find a tool whose stored hash matches.

2. **Per-tool Prefab renderer URIs.** Each prefab tool gets a unique renderer
   resource at ``ui://prefab/tool/<hash>/renderer.html``. ``list_resources``
   and ``read_resource`` synthesize these on demand from the tool's meta.

The hash is computed at registration time from ``(app_name, tool_name)`` —
both known at that moment — and stored in ``meta["fastmcp"]["tool_hash"]``.
Deterministic across replicas (same code → same hash), no registry walk
needed.

The key is deliberately public. Keys prefixed with ``_`` inside the
``fastmcp`` meta namespace are stripped at every serialization boundary
(see ``FastMCPComponent.get_meta``) because they hold process-local state
such as enabled/disabled marks. The hash is the opposite: a stable
identity that intermediaries need in order to recognize a tool they are
forwarding, so it must survive the wire.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp.tools.base import Tool

#: Length of the hex hash prefix used in URIs and backend-tool names.
HASH_LENGTH = 12

#: Key inside the ``fastmcp`` meta namespace holding a tool's identity hash.
TOOL_HASH_META_KEY = "tool_hash"


def hash_tool(app_name: str, tool_name: str) -> str:
    """Deterministic hex hash for a tool in an app.

    Same inputs on every replica produce the same output.
    """
    payload = f"{app_name}\x00{tool_name}".encode()
    return hashlib.sha256(payload).hexdigest()[:HASH_LENGTH]


def tool_identity(tool: Tool) -> str | None:
    """Read a tool's stored identity hash, if it carries one."""
    meta = tool.meta
    if not meta:
        return None
    fastmcp_meta = meta.get("fastmcp")
    if not isinstance(fastmcp_meta, dict):
        return None
    identity = fastmcp_meta.get(TOOL_HASH_META_KEY)
    return identity if isinstance(identity, str) else None


def hashed_backend_name(app_name: str, tool_name: str) -> str:
    """Format the universal name for a backend tool: ``<hash>_<local_name>``."""
    return f"{hash_tool(app_name, tool_name)}_{tool_name}"


def parse_hashed_backend_name(name: str) -> tuple[str, str] | None:
    """Parse ``<HASH_LENGTH hex>_<rest>`` → ``(hash, local_tool_name)`` or None."""
    if len(name) <= HASH_LENGTH + 1:
        return None
    prefix = name[:HASH_LENGTH]
    if name[HASH_LENGTH] != "_":
        return None
    if not all(c in "0123456789abcdef" for c in prefix):
        return None
    return prefix, name[HASH_LENGTH + 1 :]


def hashed_resource_uri(app_name: str, tool_name: str) -> str:
    """Per-tool Prefab renderer resource URI."""
    return f"ui://prefab/tool/{hash_tool(app_name, tool_name)}/renderer.html"


def parse_hashed_resource_uri(uri: str) -> str | None:
    """Extract the hash from a Prefab renderer URI, or None."""
    prefix = "ui://prefab/tool/"
    suffix = "/renderer.html"
    if not uri.startswith(prefix) or not uri.endswith(suffix):
        return None
    h = uri[len(prefix) : -len(suffix)]
    if len(h) != HASH_LENGTH or not all(c in "0123456789abcdef" for c in h):
        return None
    return h
