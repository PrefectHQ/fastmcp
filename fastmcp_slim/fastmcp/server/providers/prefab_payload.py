"""Late-bound tool names in Prefab UI payloads.

A Prefab UI is serialized during the entry tool's call, deep inside whatever
composition the server happens to have. At that moment nothing knows what the
backend tools will be *called* by the time the payload reaches a host: every
layer above may rename them, and the outermost layer's names are the only ones
a client can actually invoke.

So the payload leaves the app addressed by identity — ``<hash>_<local_name>``,
stable everywhere — and every FastMCP server rewrites those references on the
way out to whatever it lists that tool as. Servers rewrite innermost-first, so
the edge writes last and wins.

Rewriting a name in place would destroy the identity for the next layer up, so
the payload carries a name-to-identity map under ``_meta.fastmcp.toolNames``.
Each layer resolves through the map and updates it. The action objects keep the
exact shape ``prefab_ui`` defines — only the value of ``tool`` changes, and only
ever to another valid tool name.

Renderers read ``_meta`` already and ignore keys they don't recognize, so this
needs no renderer change.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp.server.providers.addressing import parse_hashed_backend_name

#: Action discriminator emitted by ``prefab_ui``'s ``CallTool``.
_TOOL_CALL_ACTION = "toolCall"

_META_KEY = "_meta"
_FASTMCP_KEY = "fastmcp"
_TOOL_NAMES_KEY = "toolNames"

#: Resolves an identity hash to the name this server lists that tool under.
#: Returns None when the identity cannot be resolved here, in which case the
#: existing reference is left alone.
IdentityResolver = Callable[[str], str | None]


def _walk_tool_calls(node: Any) -> list[dict[str, Any]]:
    """Collect every ``toolCall`` action object in a payload tree."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("action") == _TOOL_CALL_ACTION and isinstance(
            node.get("tool"), str
        ):
            found.append(node)
        for value in node.values():
            found.extend(_walk_tool_calls(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_tool_calls(item))
    return found


def _read_map(payload: dict[str, Any]) -> dict[str, str]:
    meta = payload.get(_META_KEY)
    if not isinstance(meta, dict):
        return {}
    fastmcp_meta = meta.get(_FASTMCP_KEY)
    if not isinstance(fastmcp_meta, dict):
        return {}
    names = fastmcp_meta.get(_TOOL_NAMES_KEY)
    if not isinstance(names, dict):
        return {}
    return {k: v for k, v in names.items() if isinstance(k, str) and isinstance(v, str)}


def _write_map(payload: dict[str, Any], names: dict[str, str]) -> None:
    meta = payload.setdefault(_META_KEY, {})
    if not isinstance(meta, dict):
        return
    fastmcp_meta = meta.setdefault(_FASTMCP_KEY, {})
    if not isinstance(fastmcp_meta, dict):
        return
    fastmcp_meta[_TOOL_NAMES_KEY] = names


def payload_has_identities(payload: Any) -> bool:
    """Cheap guard: does this payload carry tool references worth rewriting?

    Runs on every tool result, so it must not walk the tree.
    """
    return isinstance(payload, dict) and bool(_read_map(payload))


def annotate_payload_identities(payload: dict[str, Any]) -> dict[str, Any]:
    """Record the identity-addressed form of each reference, at serialization.

    References start out as ``<hash>_<local_name>``, so the map begins as an
    identity map to itself. Once a later layer rewrites a name, this is the
    only remaining route back: it carries both what the reference points at
    and the address any server can fall back to.
    """
    if not isinstance(payload, dict):
        return payload

    addresses: dict[str, str] = dict(_read_map(payload))
    for action in _walk_tool_calls(payload):
        tool_name = action["tool"]
        if tool_name in addresses:
            continue
        if parse_hashed_backend_name(tool_name) is not None:
            addresses[tool_name] = tool_name

    if addresses:
        _write_map(payload, addresses)
    return payload


def rewrite_payload_tool_names(
    payload: Any,
    resolve: IdentityResolver,
) -> Any:
    """Re-address a payload's tool references to this server's own names.

    Mutates in place and returns the payload.

    A reference this server cannot resolve is restored to its
    identity-addressed form rather than left as-is. Leaving it would strand
    whatever name an inner server chose — a name that is correct there and
    meaningless here — and, unlike the identity form, a stranded name has no
    route back. Restoring keeps the reference resolvable by the dispatcher,
    or by any server further out with a better view.
    """
    if not isinstance(payload, dict):
        return payload

    addresses = _read_map(payload)
    if not addresses:
        return payload

    rebound: dict[str, str] = {}
    for current_name, address in addresses.items():
        parsed = parse_hashed_backend_name(address)
        new_name = resolve(parsed[0]) if parsed is not None else None
        if new_name is None:
            new_name = address
        if new_name != current_name:
            rebound[current_name] = new_name

    if not rebound:
        return payload

    for action in _walk_tool_calls(payload):
        new_name = rebound.get(action["tool"])
        if new_name is not None:
            action["tool"] = new_name

    _write_map(
        payload,
        {rebound.get(name, name): address for name, address in addresses.items()},
    )
    return payload
