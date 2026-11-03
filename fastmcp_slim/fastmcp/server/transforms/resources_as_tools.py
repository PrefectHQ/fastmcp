"""Transform that exposes resources as tools.

This transform generates tools for listing and reading resources, enabling
clients that only support tools to access resource functionality.

The generated tools route through `ctx.fastmcp` at runtime, so all server
middleware (auth, visibility, rate limiting, etc.) applies to resource
operations exactly as it would for direct `resources/read` calls.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.transforms import ResourcesAsTools

    mcp = FastMCP("Server")
    mcp.add_transform(ResourcesAsTools(mcp))
    # Now has list_resources and read_resource tools
    ```
"""

from __future__ import annotations

import base64
import contextlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any

from mcp_types import InputRequiredResult, ToolAnnotations

from fastmcp.resources.base import InputRequiredResourceResult
from fastmcp.server.dependencies import get_context
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec, dedupe_with_versions

_DEFAULT_ANNOTATIONS = ToolAnnotations(read_only_hint=True)

if TYPE_CHECKING:
    from fastmcp.server.providers.base import Provider


class ResourcesAsTools(Transform):
    """Transform that adds tools for listing and reading resources.

    Generates two tools:
    - `list_resources`: Lists all resources and templates
    - `read_resource`: Reads a resource by URI

    The generated tools route through the server at runtime, so auth,
    middleware, and visibility apply automatically.

    This transform should be applied to a FastMCP server instance, not
    a raw Provider, because the generated tools need the server's
    middleware chain for auth and visibility filtering.

    Example:
        ```python
        mcp = FastMCP("Server")
        mcp.add_transform(ResourcesAsTools(mcp))
        # Now has list_resources and read_resource tools
        ```
    """

    def __init__(self, provider: Provider) -> None:
        from fastmcp.server.server import FastMCP

        if not isinstance(provider, FastMCP):
            raise TypeError(
                "ResourcesAsTools requires a FastMCP server instance, not a"
                f" {type(provider).__name__}. The generated tools route through"
                " the server's middleware chain at runtime for auth and"
                " visibility. Pass your FastMCP server: ResourcesAsTools(mcp)"
            )
        self._provider = provider

    def __repr__(self) -> str:
        return f"ResourcesAsTools({self._provider!r})"

    async def _is_tool_finally_enabled(self, tool: Tool) -> bool:
        """Check if tool is enabled and will not be disabled by subsequent or session visibility transforms."""
        from fastmcp.server.transforms.visibility import (
            Visibility,
            apply_session_transforms,
            is_enabled,
        )

        if not is_enabled(tool):
            return False

        transforms = getattr(self._provider, "transforms", [])
        try:
            idx = transforms.index(self)
            subsequent = transforms[idx + 1 :]
        except ValueError:
            subsequent = []

        current_tool = tool
        for t in subsequent:
            if isinstance(t, Visibility):
                current_tool = t._mark_component(current_tool)

        session_tools = await apply_session_transforms([current_tool])
        return is_enabled(session_tools[0])

    async def _is_tool_hidden_by_search(self, tool: Tool) -> bool:
        """Check if a specific tool is hidden by search, catalog transforms, or child middleware."""
        from fastmcp.server.transforms.catalog import CatalogTransform
        from fastmcp.tools.base import Tool as ToolCls

        top_transforms = getattr(self._provider, "transforms", [])
        try:
            idx = top_transforms.index(self)
            preceding = top_transforms[:idx]
        except ValueError:
            preceding = top_transforms

        from fastmcp.server.transforms.namespace import Namespace

        def _reverse_name_through_provider(provider: Any, name: str) -> str:
            current = name
            single_t = getattr(provider, "_transform", None)
            if isinstance(single_t, Namespace):
                rev = single_t._reverse_name(current)
                if rev is not None:
                    current = rev

            for t in reversed(getattr(provider, "transforms", [])):
                if isinstance(t, Namespace):
                    rev = t._reverse_name(current)
                    if rev is not None:
                        current = rev
            return current

        async def _find_tool_provider_chain(target_tool: Tool) -> list[Any]:
            server_attr = getattr(target_tool, "_server", None)
            provider_attr = getattr(target_tool, "_provider", None)

            def _find_by_identity(providers_list: Sequence[Any]) -> list[Any] | None:
                for p in providers_list:
                    if server_attr is not None:
                        if getattr(p, "server", None) is server_attr:
                            return [p]
                        inner = getattr(p, "_inner", None)
                        if getattr(inner, "server", None) is server_attr:
                            return [p]
                    if provider_attr is not None and (
                        p is provider_attr
                        or getattr(p, "_inner", None) is provider_attr
                    ):
                        return [p]
                    sub = getattr(p, "providers", [])
                    found = _find_by_identity(sub)
                    if found is not None:
                        return [p, *found]
                return None

            if server_attr is not None or provider_attr is not None:
                by_id = _find_by_identity(getattr(self._provider, "providers", []))
                if by_id is not None:
                    return by_id

            async def _search_providers(
                providers_list: Sequence[Any], name: str
            ) -> list[Any] | None:
                for p in providers_list:
                    local_name = _reverse_name_through_provider(p, name)
                    comps = getattr(p, "_components", None)
                    if isinstance(comps, dict) and any(
                        isinstance(c, ToolCls)
                        and (
                            c is target_tool
                            or getattr(c, "key", None) == target_tool.key
                            or (
                                getattr(c, "name", None)
                                in (local_name, target_tool.name)
                                and getattr(c, "version", None) == target_tool.version
                            )
                        )
                        for c in comps.values()
                    ):
                        return [p]

                    tools_dict = getattr(p, "_tools", None)
                    if isinstance(tools_dict, dict) and any(
                        isinstance(t, ToolCls)
                        and (
                            t is target_tool
                            or getattr(t, "key", None) == target_tool.key
                            or (
                                getattr(t, "name", None)
                                in (local_name, target_tool.name)
                                and getattr(t, "version", None) == target_tool.version
                            )
                        )
                        for t in tools_dict.values()
                    ):
                        return [p]

                    sub = getattr(p, "providers", [])
                    if sub:
                        found = await _search_providers(sub, local_name)
                        if found is not None:
                            return [p, *found]

                    get_tool_fn = getattr(p, "_get_tool", None)
                    if callable(get_tool_fn):
                        with contextlib.suppress(Exception):
                            version_arg = (
                                VersionSpec(eq=target_tool.version)
                                if target_tool.version is not None
                                else None
                            )
                            p_tool = await get_tool_fn(local_name, version=version_arg)
                            if p_tool is not None and (
                                p_tool is target_tool
                                or getattr(p_tool, "key", None) == target_tool.key
                                or (
                                    p_tool.name in (local_name, target_tool.name)
                                    and p_tool.version == target_tool.version
                                )
                            ):
                                return [p]
                return None

            found = await _search_providers(
                getattr(self._provider, "providers", []), target_tool.name
            )
            if found is not None:
                return found

            local_prov = getattr(self._provider, "local_provider", None)
            if local_prov is not None:
                comps = getattr(local_prov, "_components", None)
                if isinstance(comps, dict) and any(
                    isinstance(c, ToolCls)
                    and (
                        c is target_tool
                        or getattr(c, "key", None) == target_tool.key
                        or (
                            getattr(c, "name", None) == target_tool.name
                            and getattr(c, "version", None) == target_tool.version
                        )
                    )
                    for c in comps.values()
                ):
                    return [local_prov]

            fallback = getattr(self._provider, "local_provider", None) or self._provider
            return [fallback]

        chain = await _find_tool_provider_chain(tool)
        owner = chain[-1] if chain else None
        owner_server = getattr(owner, "server", None)

        has_chain_transforms = any(getattr(p, "transforms", None) for p in chain)
        has_owner_server_transforms = bool(
            owner_server and getattr(owner_server, "transforms", [])
        )
        has_owner_server_middleware = bool(
            owner_server and getattr(owner_server, "middleware", None)
        )
        has_preceding_catalog = any(isinstance(t, CatalogTransform) for t in preceding)

        if (
            not has_chain_transforms
            and not has_owner_server_transforms
            and not has_owner_server_middleware
            and not has_preceding_catalog
        ):
            # Fast path: No catalog transforms or child middleware can hide this tool.
            # Avoids querying unrelated providers (e.g. ProxyProvider over network).
            return False

        # Load tool candidate sequence from the owning provider (or local provider if unowned)
        target_provider = (
            owner or getattr(self._provider, "local_provider", None) or self._provider
        )
        list_fn = getattr(target_provider, "_list_tools", None)
        if callable(list_fn):
            try:
                raw = await list_fn()
                current_tools = list(raw) if isinstance(raw, Sequence) else [tool]
            except Exception:
                current_tools = [tool]
        else:
            current_tools = [tool]

        for p in reversed(chain):
            for t in getattr(p, "transforms", []):
                try:
                    current_tools = list(await t.list_tools(current_tools))
                except Exception:
                    if type(t).__name__ == "CodeMode":
                        return True

        local_prov = getattr(self._provider, "local_provider", None)
        if owner is not None and owner is not local_prov:
            if not any(
                isinstance(x, ToolCls)
                and (
                    x is tool
                    or x.key == tool.key
                    or (x.name == tool.name and x.version == tool.version)
                    or (
                        getattr(tool, "_original_name", None) is not None
                        and x.name == getattr(tool, "_original_name", None)
                    )
                )
                for x in current_tools
            ):
                return True

        # Evaluate preceding transforms on self._provider (e.g. Namespace, CatalogTransform)
        for t in preceding:
            try:
                current_tools = list(await t.list_tools(current_tools))
            except Exception:
                if type(t).__name__ == "CodeMode":
                    return True

        return not any(
            isinstance(x, ToolCls)
            and (
                x is tool
                or x.key == tool.key
                or (x.name == tool.name and x.version == tool.version)
            )
            for x in current_tools
        )

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Add resource tools to the tool list."""
        from fastmcp.server.server import _component_auth_allows

        list_tool = self._make_list_resources_tool()
        read_tool = self._make_read_resource_tool()
        candidate_keys = {list_tool.key, read_tool.key}

        existing_keys: set[str] = set()
        for t in tools:
            if t.key in candidate_keys:
                if not await self._is_tool_finally_enabled(t):
                    continue
                try:
                    if await _component_auth_allows(t):
                        existing_keys.add(t.key)
                except Exception:
                    existing_keys.add(t.key)

        synthetic: list[Tool] = []
        if list_tool.key not in existing_keys:
            synthetic.append(list_tool)
        if read_tool.key not in existing_keys:
            synthetic.append(read_tool)
        return [*tools, *synthetic]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name, including generated resource tools."""
        from fastmcp.server.server import _component_auth_allows

        if name not in ("list_resources", "read_resource"):
            return await call_next(name, version=version)

        syn = (
            self._make_list_resources_tool()
            if name == "list_resources"
            else self._make_read_resource_tool()
        )
        syn_matches = version is None or version.matches(syn.version)

        existing = await call_next(name, version=version)
        if existing is not None and await self._is_tool_finally_enabled(existing):
            try:
                allowed = await _component_auth_allows(existing)
            except Exception:
                allowed = True
            if allowed:
                if syn_matches and await self._is_tool_hidden_by_search(existing):
                    return syn
                return existing

        if syn_matches:
            return syn

        return None

    def _make_list_resources_tool(self) -> Tool:
        """Create the list_resources tool."""

        async def list_resources() -> str:
            """List all available resources and resource templates.

            Returns JSON with resource metadata. Static resources have a
            'uri' field, while templates have a 'uri_template' field with
            placeholders like {name}.
            """
            ctx = get_context()
            resources = dedupe_with_versions(
                await ctx.fastmcp.list_resources(), lambda r: str(r.uri)
            )
            templates = dedupe_with_versions(
                await ctx.fastmcp.list_resource_templates(), lambda t: t.uri_template
            )

            result: list[dict[str, Any]] = []

            for r in resources:
                result.append(  # noqa: PERF401
                    {
                        "uri": str(r.uri),
                        "name": r.name,
                        "description": r.description,
                        "mime_type": r.mime_type,
                    }
                )

            for t in templates:
                result.append(  # noqa: PERF401
                    {
                        "uri_template": t.uri_template,
                        "name": t.name,
                        "description": t.description,
                    }
                )

            return json.dumps(result, indent=2)

        return Tool.from_function(fn=list_resources, annotations=_DEFAULT_ANNOTATIONS)

    def _make_read_resource_tool(self) -> Tool:
        """Create the read_resource tool."""

        async def read_resource(
            uri: Annotated[str, "The URI of the resource to read"],
        ) -> str | InputRequiredResult:
            """Read a resource by its URI.

            For static resources, provide the exact URI. For templated
            resources, provide the URI with template parameters filled in.

            Returns the resource content as a string. Binary content is
            base64-encoded.
            """
            ctx = get_context()
            result = await ctx.fastmcp.read_resource(uri)
            if isinstance(result, InputRequiredResourceResult):
                return result.input_required
            return _format_result(result)

        return Tool.from_function(fn=read_resource, annotations=_DEFAULT_ANNOTATIONS)


def _format_result(result: Any) -> str:
    """Format ResourceResult for tool output.

    Single text content is returned as-is. Single binary content is
    base64-encoded. Multiple contents are JSON-encoded.
    """
    if len(result.contents) == 1:
        content = result.contents[0].content
        if isinstance(content, bytes):
            return base64.b64encode(content).decode()
        return content

    return json.dumps(
        [
            {
                "content": (
                    c.content
                    if isinstance(c.content, str)
                    else base64.b64encode(c.content).decode()
                ),
                "mime_type": c.mime_type,
            }
            for c in result.contents
        ]
    )
