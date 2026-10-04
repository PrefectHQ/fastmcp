"""Deterministic tool hashing for backend-tool routing and per-tool resources.

Each FastMCPApp backend tool gets a deterministic hash computed from its
app name + tool name. The hash serves two purposes:

1. **Backend-tool routing.** Tools with ``"app"`` in their visibility are
   callable via ``<hash>_<local_name>``. The dispatcher parses the prefix
   and calls ``get_tool_by_hash``, which finds the tool whose stored hash
   matches and applies the same transforms, visibility, and auth as a lookup
   by name.

2. **Per-tool Prefab renderer URIs.** Each prefab tool gets a unique renderer
   resource at ``ui://prefab/tool/<hash>/renderer.html``. ``list_resources``
   and ``read_resource`` synthesize these on demand from the tool's meta.

The hash is computed at registration time from ``(app_name, tool_name)`` —
both known at that moment — and stored in ``meta["fastmcp"]["_tool_hash"]``.
Deterministic across replicas (same code → same hash), no registry walk
needed.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from fastmcp.tools.tool_transform import TransformedTool

if TYPE_CHECKING:
    from fastmcp.tools.base import Tool

#: Length of the hex hash prefix used in URIs and backend-tool names.
HASH_LENGTH = 12


def hash_tool(app_name: str, tool_name: str) -> str:
    """Deterministic hex hash for a tool in an app.

    Same inputs on every replica produce the same output.
    """
    payload = f"{app_name}\x00{tool_name}".encode()
    return hashlib.sha256(payload).hexdigest()[:HASH_LENGTH]


def tool_identity(tool: Tool) -> str | None:
    """Read a tool's identity hash, if it or the tool it was transformed from carries one.

    A transform that replaces a tool's meta drops the stored hash, so the
    identity is read from the tool it was transformed from when the tool
    itself carries none.
    """
    meta = tool.meta
    if meta:
        fastmcp_meta = meta.get("fastmcp")
        if isinstance(fastmcp_meta, dict):
            identity = fastmcp_meta.get("_tool_hash")
            if isinstance(identity, str):
                return identity
    if isinstance(tool, TransformedTool):
        return tool_identity(tool.parent_tool)
    return None


def is_app_tool_with_identity(tool: Tool, tool_hash: str) -> bool:
    """Whether a tool carries this identity hash and is callable by apps."""
    meta = tool.meta or {}
    ui_meta = meta.get("ui")
    visibility = ui_meta.get("visibility", []) if isinstance(ui_meta, dict) else []
    return tool_identity(tool) == tool_hash and "app" in visibility


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
