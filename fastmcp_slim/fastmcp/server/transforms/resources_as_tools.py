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
        """Check if a specific tool is hidden by a search or catalog transform on its provider path."""
        from fastmcp.server.transforms.catalog import CatalogTransform
        from fastmcp.server.transforms.namespace import Namespace
        from fastmcp.tools.base import Tool as ToolCls

        async def _transforms_hide(
            provider: Any,
            transforms: Sequence[Any],
            target_tool: Tool,
            local_name: str,
        ) -> bool:
            candidate_tools: list[Tool] | None = None
            list_fn = getattr(provider, "_list_tools", None)
            if callable(list_fn):
                with contextlib.suppress(Exception):
                    raw = await list_fn()
                    if isinstance(raw, Sequence):
                        candidate_tools = list(raw)

            current_tools = (
                candidate_tools if candidate_tools is not None else [target_tool]
            )

            for t in transforms:
                if isinstance(t, CatalogTransform):
                    always_visible = getattr(t, "_always_visible", None)
                    if always_visible is not None:
                        if local_name not in always_visible:
                            return True
                        continue
                    try:
                        res = await t.transform_tools(current_tools)
                        if not any(
                            getattr(x, "name", None) == local_name
                            and getattr(x, "version", None) == target_tool.version
                            for x in res
                        ):
                            return True
                        current_tools = list(res)
                    except Exception:
                        if type(t).__name__ == "CodeMode":
                            return True
            return False

        async def _provider_owns_tool(
            provider: Any, target_tool: Tool, local_name: str
        ) -> bool:
            if getattr(provider, "providers", None):
                return False

            orig_name = getattr(target_tool, "_original_name", None) or target_tool.name
            allowed_names = {local_name, orig_name, target_tool.name}

            if (
                getattr(target_tool, "_server", None) is provider
                or getattr(target_tool, "_provider", None) is provider
            ):
                return True

            comps = getattr(provider, "_components", None)
            if isinstance(comps, dict):
                for c in comps.values():
                    if isinstance(c, ToolCls) and (
                        c is target_tool
                        or c.key == target_tool.key
                        or (
                            c.name in allowed_names and c.version == target_tool.version
                        )
                    ):
                        return True

            tools_dict = getattr(provider, "_tools", None)
            if isinstance(tools_dict, dict):
                for t in tools_dict.values():
                    if isinstance(t, ToolCls) and (
                        t is target_tool
                        or t.key == target_tool.key
                        or (
                            t.name in allowed_names and t.version == target_tool.version
                        )
                    ):
                        return True

            cache = getattr(provider, "_tools_cache", None)
            if cache is not None:
                items = getattr(cache, "items", None)
                if isinstance(items, list):
                    for t in items:
                        if isinstance(t, ToolCls) and (
                            t is target_tool
                            or t.key == target_tool.key
                            or (
                                t.name in allowed_names
                                and t.version == target_tool.version
                            )
                        ):
                            return True

            get_tool_fn = getattr(provider, "_get_tool", None)
            if callable(get_tool_fn):
                with contextlib.suppress(Exception):
                    version_arg = (
                        VersionSpec(eq=target_tool.version)
                        if target_tool.version is not None
                        else None
                    )
                    p_tool = await get_tool_fn(local_name, version=version_arg)
                    if p_tool is not None and isinstance(p_tool, ToolCls):
                        if (
                            p_tool is target_tool
                            or p_tool.key == target_tool.key
                            or (
                                p_tool.name in allowed_names
                                and p_tool.version == target_tool.version
                            )
                        ):
                            return True

            return False

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

        top_transforms = getattr(self._provider, "transforms", [])
        try:
            idx = top_transforms.index(self)
            preceding = top_transforms[:idx]
        except ValueError:
            preceding = top_transforms

        if await _transforms_hide(self._provider, preceding, tool, tool.name):
            return True

        async def _find_provider_hiding(
            provider: Any,
            current_name: str,
            visited: set[int] | None = None,
        ) -> bool | None:
            if provider is None:
                return None
            if visited is None:
                visited = set()
            pid = id(provider)
            if pid in visited:
                return None
            visited.add(pid)

            has_tool = await _provider_owns_tool(provider, tool, current_name)

            inner = getattr(provider, "_inner", None)
            if inner is not None:
                inner_name = _reverse_name_through_provider(provider, current_name)
                inner_hides = await _find_provider_hiding(inner, inner_name, visited)
            else:
                inner_hides = None

            mounted_server = getattr(provider, "server", None)
            if mounted_server is not None:
                mounted_name = _reverse_name_through_provider(provider, current_name)
                mounted_hides = await _find_provider_hiding(
                    mounted_server, mounted_name, visited
                )
            else:
                mounted_hides = None

            child_hides = None
            for child in getattr(provider, "providers", []):
                child_name = _reverse_name_through_provider(child, current_name)
                res = await _find_provider_hiding(child, child_name, visited)
                if res is not None:
                    child_hides = res
                    break

            if (
                has_tool
                or inner_hides is not None
                or mounted_hides is not None
                or child_hides is not None
            ):
                if await _transforms_hide(
                    provider,
                    getattr(provider, "transforms", []),
                    tool,
                    current_name,
                ):
                    return True
                return bool(
                    inner_hides is True or mounted_hides is True or child_hides is True
                )

            return None

        for child in getattr(self._provider, "providers", []):
            child_name = _reverse_name_through_provider(child, tool.name)
            res = await _find_provider_hiding(child, child_name)
            if res is not None:
                return res

        inner = getattr(self._provider, "_inner", None)
        if inner is not None:
            inner_name = _reverse_name_through_provider(self._provider, tool.name)
            res = await _find_provider_hiding(inner, inner_name)
            if res is not None:
                return res

        mounted = getattr(self._provider, "server", None)
        if mounted is not None:
            mounted_name = _reverse_name_through_provider(self._provider, tool.name)
            res = await _find_provider_hiding(mounted, mounted_name)
            if res is not None:
                return res

        return False

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
