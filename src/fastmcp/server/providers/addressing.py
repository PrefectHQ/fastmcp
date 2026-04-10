"""Internal addressing primitives for the provider graph.

FastMCP gives every mount point in a server's provider tree an internal
positional address — a tuple of integers describing the path from the root
through ``providers`` lists to the target provider. Combined with a tool
name, this address feeds a deterministic hash that uniquely identifies a
specific tool at a specific position. The hash is used in two places:

1. **Backend-tool routing.** Tools with ``"app"`` in their visibility list
   are not in ``list_tools`` output, but they need to be callable from a
   Prefab UI that references them. Their universal name is
   ``<hash>_<local_name>`` — the dispatcher recognizes that form, looks up
   the matching ``(provider, tool)`` via the reverse-hash map, and routes
   the call directly through ``provider._get_tool``, bypassing transforms.

2. **Per-tool Prefab renderer URIs.** Tools that produce Prefab content
   need a unique renderer resource URI per (mount point, tool) pair. The
   URI is ``ui://prefab/tool/<hash>/renderer.html``. ``read_resource``
   intercepts those URIs, looks up the matching tool, and synthesizes the
   renderer HTML + CSP from the tool's stored metadata. ``list_resources``
   enumerates the same URIs by walking the registry.

Both uses share the same registry, the same hash function, and the same
reverse-hash map. Nothing is mutated and nothing is materialized — the
registry is built once on first access (cheap pure walk), invalidated when
``add_provider`` runs, and rebuilt lazily next time something asks.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from fastmcp.server.providers.base import Provider
    from fastmcp.server.server import FastMCP

#: Length of the hex hash prefix used in URIs and backend-tool names.
#: 12 chars of sha256 gives ~10^14 distinct values — collision-safe for
#: any realistic provider graph.
HASH_LENGTH = 12

#: A positional address: tuple of integer indices from the root.
#: ``()`` is the root server's local provider; ``(0,)`` is the first
#: top-level provider after the root's own LocalProvider; ``(0, 2)`` is the
#: third child of that provider; etc.
AddressPath = tuple[int, ...]


class HashEntry(NamedTuple):
    """Reverse-hash map entry: enough info to resolve a hashed reference."""

    address: AddressPath
    provider: Provider
    tool_name: str
    fn: Any  #: underlying callable for FunctionTools, None for others


def hash_tool_address(address: AddressPath, tool_name: str) -> str:
    """Deterministic hex hash for a tool at a specific address.

    Inputs: positional address tuple + the tool's local name within that
    provider. Output: a fixed-length lowercase hex string. Same code on
    every replica produces the same hash for the same logical tool, so
    URIs and backend-tool names round-trip across horizontally-scaled
    deployments without coordination.
    """
    # Use a delimiter that can't appear inside the inputs to ensure
    # ("a", "b_c") and ("a_b", "c") hash differently.
    payload = f"{address!r}\x00{tool_name}".encode()
    return hashlib.sha256(payload).hexdigest()[:HASH_LENGTH]


def hashed_backend_name(address: AddressPath, tool_name: str) -> str:
    """Format the universal name for a backend tool: ``<hash>_<local_name>``."""
    return f"{hash_tool_address(address, tool_name)}_{tool_name}"


def parse_hashed_backend_name(name: str) -> tuple[str, str] | None:
    """Reverse of :func:`hashed_backend_name`.

    Returns ``(hash, local_tool_name)`` if *name* matches the
    ``<HASH_LENGTH hex>_<rest>`` shape, else ``None``. The dispatcher uses
    this to detect hashed-name calls and look them up via the reverse map;
    if the prefix isn't valid hex of the right length, or the hash isn't
    in the reverse map, the dispatcher falls through to normal resolution.
    """
    if len(name) <= HASH_LENGTH + 1:
        return None
    prefix = name[:HASH_LENGTH]
    if name[HASH_LENGTH] != "_":
        return None
    # All hex characters?
    if not all(c in "0123456789abcdef" for c in prefix):
        return None
    return prefix, name[HASH_LENGTH + 1 :]


def hashed_resource_uri(address: AddressPath, tool_name: str) -> str:
    """Format the per-tool Prefab renderer resource URI."""
    return f"ui://prefab/tool/{hash_tool_address(address, tool_name)}/renderer.html"


def parse_hashed_resource_uri(uri: str) -> str | None:
    """Extract the hash from a Prefab renderer URI, or ``None`` if it doesn't match."""
    prefix = "ui://prefab/tool/"
    suffix = "/renderer.html"
    if not uri.startswith(prefix) or not uri.endswith(suffix):
        return None
    h = uri[len(prefix) : -len(suffix)]
    if len(h) != HASH_LENGTH or not all(c in "0123456789abcdef" for c in h):
        return None
    return h


def _iter_children(provider: Provider) -> Sequence[Provider] | None:
    """Return the provider's sub-providers if it's a container, else ``None``.

    Containers are ``AggregateProvider`` (holds a list in ``self.providers``)
    and ``_WrappedProvider`` (transparently wraps another provider with a
    transform — the wrapper shares the wrapped provider's address segment).
    Other provider types are leaves for addressing purposes.
    """
    # Local imports to avoid a circular import at module load.
    from fastmcp.server.providers.aggregate import AggregateProvider
    from fastmcp.server.providers.wrapped_provider import _WrappedProvider

    if isinstance(provider, _WrappedProvider):
        return _iter_children(provider._inner)
    if isinstance(provider, AggregateProvider):
        return list(provider.providers)
    return None


def build_address_registry(root: FastMCP) -> dict[AddressPath, Provider]:
    """Walk the provider graph and assign each mount point a positional address.

    Pure: returns a fresh dict, doesn't touch any provider, doesn't
    instantiate resources, doesn't mutate metadata. The walk visits
    children in registration order; each provider receives its index in
    its parent's child list as the next segment of its address path.

    The root's own internal LocalProvider is treated as address-transparent
    — tools registered directly via ``@mcp.tool`` live at the empty path
    ``()`` rather than getting their own segment. Every other provider,
    including user-added LocalProviders, contributes a segment.
    """
    registry: dict[AddressPath, Provider] = {}
    registry[()] = root._local_provider

    def visit(providers: Sequence[Provider], base_path: AddressPath) -> None:
        for index, provider in enumerate(providers):
            path = (*base_path, index)
            registry[path] = provider
            children = _iter_children(provider)
            if children is not None:
                visit(children, path)

    # Skip the root's transparent LocalProvider when walking top-level children.
    top_level = [p for p in root.providers if p is not root._local_provider]
    if top_level:
        visit(top_level, ())

    return registry


class ReverseHashMaps(NamedTuple):
    """Both reverse-lookup indexes produced by :func:`build_reverse_hash_map`.

    ``by_hash``: ``hash_hex → HashEntry`` — used by the dispatcher for
    hashed-name calls and by ``read_resource`` for prefab URI lookups.

    ``by_callable``: ``id(fn) → HashEntry`` — used by the Prefab
    peer-reference resolver to look up a peer tool's address by the
    original function identity, without needing ``Context.mount_path``
    or a name-based reverse walk.
    """

    by_hash: dict[str, HashEntry]
    by_callable: dict[int, HashEntry]


def build_reverse_hash_map(
    registry: dict[AddressPath, Provider],
) -> ReverseHashMaps:
    """Build both reverse-lookup indexes from the address registry.

    Walks every provider in the registry, reads its synchronous tool
    storage, and for each tool computes its hash and (for FunctionTools)
    captures the underlying callable. The result lets the dispatcher,
    ``read_resource``, and the Prefab resolver do O(1) lookups.

    Tools that aren't directly stored in a ``LocalProvider`` (or a
    ``FastMCPApp``'s internal ``_local``) won't appear. That's
    intentional — the addressing system is for tools you register, not
    for tools dynamically synthesized at list time by custom providers.
    """
    by_hash: dict[str, HashEntry] = {}
    by_callable: dict[int, HashEntry] = {}
    for address, provider in registry.items():
        for tool_name, fn in _enumerate_tools(provider):
            entry = HashEntry(
                address=address,
                provider=provider,
                tool_name=tool_name,
                fn=fn,
            )
            digest = hash_tool_address(address, tool_name)
            by_hash[digest] = entry
            if fn is not None:
                by_callable[id(fn)] = entry
    return ReverseHashMaps(by_hash=by_hash, by_callable=by_callable)


def _enumerate_tools(provider: Provider) -> list[tuple[str, Any]]:
    """Synchronously enumerate ``(name, fn_or_None)`` for tools in *provider*.

    Reaches into the underlying ``LocalProvider`` (unwrapping any
    ``_WrappedProvider`` transform layers and looking through
    ``FastMCPApp._local``). ``fn`` is the underlying callable for
    ``FunctionTool`` instances, ``None`` for other Tool subclasses.
    """
    from fastmcp.apps.app import FastMCPApp
    from fastmcp.server.providers.local_provider import LocalProvider
    from fastmcp.server.providers.wrapped_provider import _WrappedProvider
    from fastmcp.tools.base import Tool
    from fastmcp.tools.function_tool import FunctionTool

    inner: Provider = provider
    while isinstance(inner, _WrappedProvider):
        inner = inner._inner

    sources: list[LocalProvider] = []
    if isinstance(inner, LocalProvider):
        sources.append(inner)
    if isinstance(inner, FastMCPApp):
        sources.append(inner._local)

    results: list[tuple[str, Any]] = []
    for src in sources:
        for component in src._components.values():
            if isinstance(component, Tool):
                fn = component.fn if isinstance(component, FunctionTool) else None
                results.append((component.name, fn))
    return results
