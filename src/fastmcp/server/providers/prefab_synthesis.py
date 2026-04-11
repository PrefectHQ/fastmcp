"""On-demand Prefab renderer resource synthesis.

Tools marked as Prefab (via ``app=True``, ``PrefabAppConfig``, etc.) carry
a placeholder ``meta.ui.resourceUri`` and optionally a hash in
``meta.fastmcp._tool_hash``. This module synthesizes per-tool renderer
resources on demand at ``list_resources`` and ``read_resource`` time
without storing or materializing anything.

Each tool's resource URI is ``ui://prefab/tool/<hash>/renderer.html``
where the hash comes from the tool's own meta (set at registration from
the app name + tool name). CSP on the resource is the tool's
``meta.ui.csp`` merged with the renderer defaults across all four
``*_domains`` fields.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.server.providers.addressing import (
    HASH_LENGTH,
    hash_tool,
    parse_hashed_resource_uri,
)

if TYPE_CHECKING:
    from fastmcp.resources.base import Resource
    from fastmcp.server.server import FastMCP
    from fastmcp.tools.base import Tool

#: The placeholder URI that decorators stamp on tools needing a renderer.
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


def _get_tool_hash(tool: Tool) -> str | None:
    """Read the stored hash from tool meta, or compute from app name + tool name."""
    meta = tool.meta or {}
    fastmcp_meta = meta.get("fastmcp")
    if isinstance(fastmcp_meta, dict):
        h = fastmcp_meta.get("_tool_hash")
        if isinstance(h, str) and len(h) == HASH_LENGTH:
            return h
        # Fall back to computing from app name
        app = fastmcp_meta.get("app")
        if isinstance(app, str):
            return hash_tool(app, tool.name)
    # Root-level prefab tool (no app name) — hash from empty prefix.
    return hash_tool("", tool.name)


def _merge_domain_lists(
    base: list[str] | None, extra: list[str] | None
) -> list[str] | None:
    if base is None and extra is None:
        return None
    combined = list(base or [])
    for item in extra or []:
        if item not in combined:
            combined.append(item)
    return combined or None


def _build_resource_for_tool(tool: Tool) -> Resource | None:
    """Synthesize a TextResource for a prefab tool. Returns None if prefab_ui isn't installed."""
    try:
        from prefab_ui.renderer import get_renderer_csp, get_renderer_html
    except ImportError:
        return None

    from fastmcp.apps.config import (
        UI_MIME_TYPE,
        AppConfig,
        ResourceCSP,
        app_config_to_meta_dict,
    )
    from fastmcp.resources.types import TextResource

    tool_hash = _get_tool_hash(tool)
    if tool_hash is None:
        return None

    # Merge user CSP with renderer defaults — all four domain fields.
    defaults: dict[str, Any] = get_renderer_csp() or {}
    user_csp: dict[str, Any] = {}
    if tool.meta and isinstance(tool.meta.get("ui"), dict):
        raw = tool.meta["ui"].get("csp")
        if isinstance(raw, dict):
            user_csp = raw

    def _get(d: dict[str, Any], snake: str, camel: str) -> list[str] | None:
        val = d.get(snake)
        if val is None:
            val = d.get(camel)
        return val if isinstance(val, list) else None

    merged = {
        "connect_domains": _merge_domain_lists(
            defaults.get("connect_domains"),
            _get(user_csp, "connect_domains", "connectDomains"),
        ),
        "resource_domains": _merge_domain_lists(
            defaults.get("resource_domains"),
            _get(user_csp, "resource_domains", "resourceDomains"),
        ),
        "frame_domains": _merge_domain_lists(
            defaults.get("frame_domains"),
            _get(user_csp, "frame_domains", "frameDomains"),
        ),
        "base_uri_domains": _merge_domain_lists(
            defaults.get("base_uri_domains"),
            _get(user_csp, "base_uri_domains", "baseUriDomains"),
        ),
    }

    resource_csp = ResourceCSP(**merged) if any(merged.values()) else None

    # Carry permissions from the tool's meta to the resource (same
    # principle as CSP — belongs on the resource, not the tool).
    user_permissions = None
    if tool.meta and isinstance(tool.meta.get("ui"), dict):
        raw_perms = tool.meta["ui"].get("permissions")
        if isinstance(raw_perms, dict):
            from fastmcp.apps.config import ResourcePermissions

            user_permissions = ResourcePermissions(**raw_perms)

    resource_app = AppConfig(
        csp=resource_csp,
        permissions=user_permissions,
    )
    uri = f"ui://prefab/tool/{tool_hash}/renderer.html"

    return TextResource(
        uri=uri,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        name=f"Prefab Renderer ({tool.name})",
        text=get_renderer_html(),
        mime_type=UI_MIME_TYPE,
        meta={"ui": app_config_to_meta_dict(resource_app)},
    )


def _walk_prefab_tools(server: FastMCP) -> list[Tool]:
    """Enumerate all prefab tools across the server's providers (sync walk of _components)."""
    from fastmcp.apps.app import FastMCPApp
    from fastmcp.server.providers.base import Provider
    from fastmcp.server.providers.local_provider import LocalProvider
    from fastmcp.server.providers.wrapped_provider import _WrappedProvider
    from fastmcp.tools.base import Tool

    results: list[Tool] = []

    def _walk_provider(provider: Provider) -> None:
        # Unwrap transform wrappers
        inner = provider
        while isinstance(inner, _WrappedProvider):
            inner = inner._inner

        # Extract tools from local storage
        sources: list[LocalProvider] = []
        if isinstance(inner, LocalProvider):
            sources.append(inner)
        if isinstance(inner, FastMCPApp):
            sources.append(inner._local)
        for src in sources:
            for component in src._components.values():
                if isinstance(component, Tool) and _is_prefab_tool(component):
                    results.append(component)

        # Recurse into aggregate children
        from fastmcp.server.providers.aggregate import AggregateProvider
        from fastmcp.server.providers.fastmcp_provider import FastMCPProvider

        if isinstance(inner, AggregateProvider):
            for child in inner.providers:
                _walk_provider(child)
        # Recurse into mounted FastMCP servers
        if isinstance(inner, FastMCPProvider):
            for child in inner.server.providers:
                _walk_provider(child)

    for provider in server.providers:
        _walk_provider(provider)

    return results


async def synthesize_prefab_resources(server: FastMCP) -> list[Resource]:
    """Return fresh synthetic Prefab resources for all prefab tools. Pure."""
    resources: list[Resource] = []
    seen_hashes: set[str] = set()
    for tool in _walk_prefab_tools(server):
        h = _get_tool_hash(tool)
        if h is None or h in seen_hashes:
            continue
        seen_hashes.add(h)
        resource = _build_resource_for_tool(tool)
        if resource is not None:
            resources.append(resource)
    return resources


async def synthesize_prefab_resource_by_uri(
    server: FastMCP, uri: str
) -> Resource | None:
    """Intercept a Prefab renderer URI and synthesize on demand."""
    digest = parse_hashed_resource_uri(uri)
    if digest is None:
        return None
    for tool in _walk_prefab_tools(server):
        if _get_tool_hash(tool) == digest:
            return _build_resource_for_tool(tool)
    return None


def rewrite_tool_meta_for_wire(tool: Tool) -> Tool:
    """Return a model_copy with the per-tool URI and CSP stripped.

    Reads the hash from the tool's own meta. If no hash is found,
    returns the tool unchanged. Produces a fresh copy — the original
    Tool object is untouched.
    """
    if not _is_prefab_tool(tool):
        return tool
    tool_hash = _get_tool_hash(tool)
    if tool_hash is None:
        return tool
    assert tool.meta is not None
    new_ui = dict(tool.meta["ui"])
    new_ui["resourceUri"] = f"ui://prefab/tool/{tool_hash}/renderer.html"
    new_ui.pop("csp", None)
    new_ui.pop("permissions", None)
    new_meta = dict(tool.meta)
    new_meta["ui"] = new_ui
    return tool.model_copy(update={"meta": new_meta})
