"""On-demand Prefab renderer resource synthesis.

Tools that opt into Prefab UI rendering (via ``@mcp.tool(app=True)``,
``@mcp.tool(app=PrefabAppConfig(...))``, ``@app.ui()``, or by returning a
Prefab type) are stamped at registration time with a placeholder
``meta.ui.resourceUri``. This module turns that mark into a real resource
at the moment something asks for it — list_resources enumerates the
synthetic resources and read_resource serves their content — without
ever creating, storing, or mutating anything ahead of time.

The URI for each tool's renderer is derived from the tool's mount-point
address via the same hash function used for backend-tool routing. Same
code on every replica produces the same URIs, so a host fetching
``ui://prefab/tool/<hash>/renderer.html`` from any replica gets a
consistent answer.

CSP comes from the tool's ``meta.ui.csp`` (set by the user via
``PrefabAppConfig(csp=...)``), merged with the renderer's defaults
across all four ``*_domains`` fields. The renderer HTML comes from
``prefab_ui.renderer.get_renderer_html()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.server.providers.addressing import (
    HASH_LENGTH,
    hash_tool_address,
    hashed_resource_uri,
    parse_hashed_resource_uri,
)

if TYPE_CHECKING:
    from fastmcp.resources.base import Resource
    from fastmcp.server.providers.base import Provider
    from fastmcp.server.server import FastMCP
    from fastmcp.tools.base import Tool

#: The placeholder URI that decorators stamp on tools to mark them as
#: needing a Prefab renderer. The synthesizer recognizes the marker and
#: rewrites it to a per-tool hashed URI at list time.
PREFAB_PLACEHOLDER_URI = "ui://prefab/renderer.html"


def _is_prefab_tool(tool: Tool) -> bool:
    """True if *tool* was marked as needing a Prefab renderer at registration."""
    meta = tool.meta
    if not meta:
        return False
    ui = meta.get("ui")
    if not isinstance(ui, dict):
        return False
    return ui.get("resourceUri") == PREFAB_PLACEHOLDER_URI


def _merge_domain_lists(
    base: list[str] | None, extra: list[str] | None
) -> list[str] | None:
    """Union two domain lists in order, deduplicating, returning None if empty."""
    if base is None and extra is None:
        return None
    combined = list(base or [])
    for item in extra or []:
        if item not in combined:
            combined.append(item)
    return combined or None


def _merged_csp_dict(
    user_csp: dict[str, Any] | None,
) -> dict[str, list[str] | None] | None:
    """Merge a tool's user CSP with the renderer's defaults across all domain fields.

    The old singleton ``_ensure_prefab_renderer`` only copied
    ``resource_domains`` and ``connect_domains`` from the renderer's
    defaults — silently dropping ``frame_domains`` and
    ``base_uri_domains`` even when the renderer declared them. This merge
    covers all four. ``user_csp`` is the dict from a tool's
    ``meta.ui.csp`` (camelCase wire form from ``app_config_to_meta_dict``)
    and is merged on top of the defaults.
    """
    try:
        from prefab_ui.renderer import get_renderer_csp
    except ImportError:
        return None

    defaults: dict[str, Any] = get_renderer_csp() or {}
    user = user_csp or {}

    def _get(d: dict[str, Any], snake: str, camel: str) -> list[str] | None:
        val = d.get(snake)
        if val is None:
            val = d.get(camel)
        return val if isinstance(val, list) else None

    merged = {
        "connect_domains": _merge_domain_lists(
            defaults.get("connect_domains"),
            _get(user, "connect_domains", "connectDomains"),
        ),
        "resource_domains": _merge_domain_lists(
            defaults.get("resource_domains"),
            _get(user, "resource_domains", "resourceDomains"),
        ),
        "frame_domains": _merge_domain_lists(
            defaults.get("frame_domains"),
            _get(user, "frame_domains", "frameDomains"),
        ),
        "base_uri_domains": _merge_domain_lists(
            defaults.get("base_uri_domains"),
            _get(user, "base_uri_domains", "baseUriDomains"),
        ),
    }

    if not any(merged.values()):
        return None
    return merged


def _build_resource_for_tool(address: tuple[int, ...], tool: Tool) -> Resource | None:
    """Synthesize a ``TextResource`` for a prefab tool at *address*.

    Reads the tool's CSP from ``meta.ui.csp`` (if any), merges with the
    renderer defaults, and produces a fresh ``TextResource`` with the
    hashed URI and the merged CSP on its meta. Returns ``None`` when
    prefab_ui isn't installed.
    """
    try:
        from prefab_ui.renderer import get_renderer_html
    except ImportError:
        return None

    from fastmcp.apps.config import (
        UI_MIME_TYPE,
        AppConfig,
        ResourceCSP,
        app_config_to_meta_dict,
    )
    from fastmcp.resources.types import TextResource

    user_csp = None
    if tool.meta and isinstance(tool.meta.get("ui"), dict):
        ui = tool.meta["ui"]
        if isinstance(ui.get("csp"), dict):
            user_csp = ui["csp"]

    merged = _merged_csp_dict(user_csp)
    resource_csp = ResourceCSP(**merged) if merged else None
    resource_app = AppConfig(csp=resource_csp) if resource_csp else AppConfig()

    return TextResource(
        uri=hashed_resource_uri(address, tool.name),  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        name=f"Prefab Renderer ({'/'.join(map(str, address)) or 'root'}/{tool.name})",
        text=get_renderer_html(),
        mime_type=UI_MIME_TYPE,
        meta={"ui": app_config_to_meta_dict(resource_app)},
    )


def _enumerate_prefab_tools(
    server: FastMCP,
) -> list[tuple[tuple[int, ...], Tool]]:
    """Walk the server's reverse-hash map and return ``(address, tool)`` for prefab tools.

    Uses the registry's reverse-hash map (which already enumerates every
    tool by hash) and filters for ones that carry the prefab placeholder.
    Each entry's ``provider`` is the leaf that owns the tool; we look up
    the actual ``Tool`` object from that provider's local storage to
    inspect its meta.
    """
    from fastmcp.apps.app import FastMCPApp
    from fastmcp.server.providers.local_provider import LocalProvider
    from fastmcp.server.providers.wrapped_provider import _WrappedProvider
    from fastmcp.tools.base import Tool

    results: list[tuple[tuple[int, ...], Tool]] = []
    for entry in server.reverse_hash_map.values():
        # Find the actual Tool object inside the leaf provider so we can
        # read its raw meta (transforms wouldn't change `meta.ui` but
        # might rename the tool).
        inner: Provider = entry.provider
        while isinstance(inner, _WrappedProvider):
            inner = inner._inner
        sources: list[LocalProvider] = []
        if isinstance(inner, LocalProvider):
            sources.append(inner)
        if isinstance(inner, FastMCPApp):
            sources.append(inner._local)
        for src in sources:
            for component in src._components.values():
                if (
                    isinstance(component, Tool)
                    and component.name == entry.tool_name
                    and _is_prefab_tool(component)
                ):
                    results.append((entry.address, component))
    return results


async def synthesize_prefab_resources(server: FastMCP) -> list[Resource]:
    """Return a fresh list of synthetic Prefab renderer resources for the server.

    One resource per (mount-point address, prefab tool) pair. Used by
    ``list_resources`` to surface the renderer resources for discovery.
    Pure: produces fresh objects on every call, no caching, no mutation.
    """
    resources: list[Resource] = []
    for address, tool in _enumerate_prefab_tools(server):
        resource = _build_resource_for_tool(address, tool)
        if resource is not None:
            resources.append(resource)
    return resources


async def synthesize_prefab_resource_by_uri(
    server: FastMCP, uri: str
) -> Resource | None:
    """Look up a synthetic Prefab renderer by URI, or return None.

    Used by ``read_resource`` to intercept ``ui://prefab/tool/<hash>/...``
    fetches. Parses the hash, finds the matching tool via the reverse
    map, and synthesizes the resource. Returns None for any URI that
    isn't a prefab renderer URI or doesn't match a known tool.
    """
    digest = parse_hashed_resource_uri(uri)
    if digest is None:
        return None

    # Walk prefab tools to find the one whose hash matches.
    for address, tool in _enumerate_prefab_tools(server):
        if hash_tool_address(address, tool.name) == digest:
            return _build_resource_for_tool(address, tool)
    return None


def rewrite_tool_meta_for_address(tool: Tool, address: tuple[int, ...]) -> Tool:
    """Return a ``model_copy`` of *tool* with prefab meta rewritten for *address*.

    If the tool isn't a prefab tool (or already has a non-placeholder
    URI), returns the original unchanged. Otherwise produces a fresh
    copy with:

    - ``meta.ui.resourceUri`` = the hashed per-tool URI for *address*
    - ``meta.ui.csp`` removed (CSP belongs on the resource side)
    - ``meta.ui.permissions`` removed for the same reason

    The original ``Tool`` object is untouched — this is a per-call view,
    not a stored mutation. Used by ``list_tools`` to give clients a
    correct, stable URI for each prefab tool.
    """
    if not _is_prefab_tool(tool):
        return tool
    # ``_is_prefab_tool`` already verified meta is a dict with a dict ui.
    assert tool.meta is not None
    new_ui = dict(tool.meta["ui"])
    new_ui["resourceUri"] = hashed_resource_uri(address, tool.name)
    new_ui.pop("csp", None)
    new_ui.pop("permissions", None)
    new_meta = dict(tool.meta)
    new_meta["ui"] = new_ui
    return tool.model_copy(update={"meta": new_meta})


# Re-exported for callers that want to recognize the URI format.
__all__ = [
    "HASH_LENGTH",
    "PREFAB_PLACEHOLDER_URI",
    "rewrite_tool_meta_for_address",
    "synthesize_prefab_resource_by_uri",
    "synthesize_prefab_resources",
]
