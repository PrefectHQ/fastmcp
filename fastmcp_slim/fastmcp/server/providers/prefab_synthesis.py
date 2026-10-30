"""On-demand Prefab renderer resources shared by effective configuration.

Tools carry a placeholder UI resource URI. Listings rewrite it to a stable
renderer URI derived from the Prefab version, delivery mode, and effective
resource metadata. Matching tools share a resource; different CSP, permissions,
domains, and border preferences stay distinct. HTML is loaded only on read.
Legacy per-tool URIs remain readable independently of backend tool identity.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from fastmcp.server.providers.addressing import (
    HASH_LENGTH,
    TOOL_HASH_META_KEY,
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
        h = fastmcp_meta.get(TOOL_HASH_META_KEY)
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
    """Build a lazy renderer resource, identified by its effective configuration."""
    try:
        from prefab_ui import __version__
        from prefab_ui.renderer import get_renderer_csp, get_renderer_html
    except ImportError:
        return None

    from fastmcp.apps.config import (
        UI_MIME_TYPE,
        AppConfig,
        ResourceCSP,
        app_config_to_meta_dict,
    )
    from fastmcp.resources.function_resource import FunctionResource

    # Mirror Prefab's documented delivery-mode precedence without loading HTML.
    mode = os.environ.get("PREFAB_RENDERER_URL", "").rstrip("/")
    if not mode:
        bundled = bool(os.environ.get("PREFAB_BUNDLED_RENDERER"))
        dev_version = "dev" in __version__ or __version__ == "0.0.0"
        mode = "bundled" if bundled or dev_version else "cdn"

    # Merge user CSP with renderer defaults — all four domain fields.
    defaults: dict[str, Any] = get_renderer_csp(mode=mode) or {}
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
    # CSP domain lists are sets of allowed origins; order has no meaning.
    merged = {
        key: sorted(set(value)) if value else None for key, value in merged.items()
    }

    resource_csp = ResourceCSP(**merged) if any(merged.values()) else None

    # Carry permissions, domain and prefersBorder from the tool's meta to the
    # resource (same principle as CSP — they belong on the resource, not the
    # tool).
    user_permissions = None
    domain = None
    prefers_border = None
    if tool.meta and isinstance(tool.meta.get("ui"), dict):
        tool_ui = tool.meta["ui"]
        raw_perms = tool_ui.get("permissions")
        if isinstance(raw_perms, dict):
            from fastmcp.apps.config import ResourcePermissions

            user_permissions = ResourcePermissions(**raw_perms)
        raw_domain = tool_ui.get("domain")
        if isinstance(raw_domain, str):
            domain = raw_domain
        raw_border = tool_ui.get("prefersBorder")
        if isinstance(raw_border, bool):
            prefers_border = raw_border

    resource_app = AppConfig(
        csp=resource_csp,
        permissions=user_permissions,
        domain=domain,
        prefers_border=prefers_border,
    )
    meta = {"ui": app_config_to_meta_dict(resource_app)}
    identity = json.dumps(
        {"version": __version__, "mode": mode, "mime_type": UI_MIME_TYPE, "meta": meta},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    uri = f"ui://prefab/renderer/{digest}/renderer.html"

    return FunctionResource(
        uri=cast(Any, uri),
        name="Prefab Renderer",
        fn=partial(get_renderer_html, mode=mode),
        mime_type=UI_MIME_TYPE,
        meta=meta,
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
            results.extend(
                component
                for component in src._components.values()
                if isinstance(component, Tool) and _is_prefab_tool(component)
            )

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


async def _effective_prefab_tools(server: FastMCP) -> list[Tool]:
    """Use provider and session transforms before deriving resource identity."""
    from fastmcp.server.providers.base import Provider
    from fastmcp.server.transforms.visibility import (
        apply_session_transforms,
        is_enabled,
    )
    from fastmcp.utilities.versions import VersionSpec

    tools = await apply_session_transforms(list(await Provider.list_tools(server)))
    result: list[Tool] = []
    for tool in tools:
        if not is_enabled(tool):
            continue
        ui = (tool.meta or {}).get("ui")
        if _is_prefab_tool(tool):
            result.append(tool)
        elif isinstance(ui, dict) and str(ui.get("resourceUri", "")).startswith(
            "ui://prefab/renderer/"
        ):
            # Mounted servers have already rewritten their listed metadata.
            # A normal lookup retains the resource fields needed for synthesis.
            original = await server.get_tool(tool.name, VersionSpec(eq=tool.version))
            if original is not None and _is_prefab_tool(original):
                result.append(original)
    return result


async def synthesize_prefab_resources(server: FastMCP) -> list[Resource]:
    """Return one lazy resource per effective renderer configuration."""
    resources: list[Resource] = []
    seen: set[str] = set()
    for tool in await _effective_prefab_tools(server):
        resource = _build_resource_for_tool(tool)
        if resource is not None and resource.key not in seen:
            seen.add(resource.key)
            resources.append(resource)
    return resources


async def synthesize_prefab_resource_by_uri(
    server: FastMCP, uri: str
) -> Resource | None:
    """Intercept a Prefab renderer URI and synthesize on demand."""
    digest = parse_hashed_resource_uri(uri)
    if digest is None and not uri.startswith("ui://prefab/renderer/"):
        return None
    tools = (
        _walk_prefab_tools(server)
        if digest is not None
        else await _effective_prefab_tools(server)
    )
    for tool in tools:
        resource = _build_resource_for_tool(tool)
        if resource is not None:
            if digest is not None and _get_tool_hash(tool) == digest:
                return resource.model_copy(update={"uri": uri})
            if str(resource.uri) == uri:
                return resource
    return None


def rewrite_tool_meta_for_wire(tool: Tool) -> Tool:
    """Return a copy with the shared URI and resource-only fields stripped."""
    if not _is_prefab_tool(tool):
        return tool
    resource = _build_resource_for_tool(tool)
    if resource is None:
        return tool
    assert tool.meta is not None
    new_ui = dict(tool.meta["ui"])
    new_ui["resourceUri"] = str(resource.uri)
    new_ui.pop("csp", None)
    new_ui.pop("permissions", None)
    new_ui.pop("domain", None)
    new_ui.pop("prefersBorder", None)
    new_meta = dict(tool.meta)
    new_meta["ui"] = new_ui
    return tool.model_copy(update={"meta": new_meta})
