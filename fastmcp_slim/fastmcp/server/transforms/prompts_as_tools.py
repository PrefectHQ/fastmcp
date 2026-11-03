"""Transform that exposes prompts as tools.

This transform generates tools for listing and getting prompts, enabling
clients that only support tools to access prompt functionality.

The generated tools route through `ctx.fastmcp` at runtime, so all server
middleware (auth, visibility, rate limiting, etc.) applies to prompt
operations exactly as it would for direct `prompts/get` calls.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.transforms import PromptsAsTools

    mcp = FastMCP("Server")
    mcp.add_transform(PromptsAsTools(mcp))
    # Now has list_prompts and get_prompt tools
    ```
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any

from mcp_types import InputRequiredResult, TextContent

from fastmcp.prompts.base import InputRequiredPromptResult
from fastmcp.server.dependencies import get_context
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec, dedupe_with_versions

if TYPE_CHECKING:
    from fastmcp.server.providers.base import Provider


class PromptsAsTools(Transform):
    """Transform that adds tools for listing and getting prompts.

    Generates two tools:
    - `list_prompts`: Lists all prompts
    - `get_prompt`: Gets a specific prompt with optional arguments

    The generated tools route through the server at runtime, so auth,
    middleware, and visibility apply automatically.

    This transform should be applied to a FastMCP server instance, not
    a raw Provider, because the generated tools need the server's
    middleware chain for auth and visibility filtering.

    Example:
        ```python
        mcp = FastMCP("Server")
        mcp.add_transform(PromptsAsTools(mcp))
        # Now has list_prompts and get_prompt tools
        ```
    """

    def __init__(self, provider: Provider) -> None:
        from fastmcp.server.server import FastMCP

        if not isinstance(provider, FastMCP):
            raise TypeError(
                "PromptsAsTools requires a FastMCP server instance, not a"
                f" {type(provider).__name__}. The generated tools route through"
                " the server's middleware chain at runtime for auth and"
                " visibility. Pass your FastMCP server: PromptsAsTools(mcp)"
            )
        self._provider = provider

    def __repr__(self) -> str:
        return f"PromptsAsTools({self._provider!r})"

    def _is_tool_hidden_by_search(self, tool: Tool) -> bool:
        """Check if a specific tool is hidden by a search or catalog transform on its provider path."""
        from fastmcp.server.transforms.catalog import CatalogTransform

        name = tool.name

        def _transforms_hide(transforms: Sequence[Any]) -> bool:
            for t in transforms:
                if isinstance(t, CatalogTransform):
                    always_visible = getattr(t, "_always_visible", None)
                    if always_visible is not None:
                        if name not in always_visible:
                            return True
                    elif (
                        type(t).transform_tools is not CatalogTransform.transform_tools
                    ):
                        return True
            return False

        top_transforms = getattr(self._provider, "transforms", [])
        try:
            idx = top_transforms.index(self)
            preceding = top_transforms[:idx]
        except ValueError:
            preceding = top_transforms

        if _transforms_hide(preceding):
            return True

        direct_components = getattr(self._provider, "_components", {})
        if tool.key in direct_components or any(
            c is tool for c in direct_components.values()
        ):
            return False

        def _find_provider_hiding(
            provider: Any, visited: set[int] | None = None
        ) -> bool | None:
            if provider is None:
                return None
            if visited is None:
                visited = set()
            pid = id(provider)
            if pid in visited:
                return None
            visited.add(pid)

            comps = getattr(provider, "_components", {})
            orig_name = getattr(tool, "_original_name", None) or tool.name
            has_tool = (
                tool.key in comps
                or any(c is tool for c in comps.values())
                or getattr(tool, "_server", None) is provider
                or any(
                    getattr(c, "name", None) == orig_name
                    and getattr(c, "version", None) == tool.version
                    for c in comps.values()
                )
            )

            inner = getattr(provider, "_inner", None)
            inner_hides = (
                _find_provider_hiding(inner, visited) if inner is not None else None
            )

            mounted_server = getattr(provider, "server", None)
            mounted_hides = (
                _find_provider_hiding(mounted_server, visited)
                if mounted_server is not None
                else None
            )

            child_hides = None
            for child in getattr(provider, "providers", []):
                res = _find_provider_hiding(child, visited)
                if res is not None:
                    child_hides = res
                    break

            if (
                has_tool
                or inner_hides is not None
                or mounted_hides is not None
                or child_hides is not None
            ):
                if _transforms_hide(getattr(provider, "transforms", [])):
                    return True
                return bool(
                    inner_hides is True or mounted_hides is True or child_hides is True
                )

            return None

        for child in getattr(self._provider, "providers", []):
            res = _find_provider_hiding(child)
            if res is not None:
                return res

        inner = getattr(self._provider, "_inner", None)
        if inner is not None:
            res = _find_provider_hiding(inner)
            if res is not None:
                return res

        mounted = getattr(self._provider, "server", None)
        if mounted is not None:
            res = _find_provider_hiding(mounted)
            if res is not None:
                return res

        return False

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Add prompt tools to the tool list."""
        from fastmcp.server.server import _tool_auth_allows
        from fastmcp.server.transforms.visibility import is_enabled

        list_tool = self._make_list_prompts_tool()
        get_tool = self._make_get_prompt_tool()
        candidate_keys = {list_tool.key, get_tool.key}

        existing_keys: set[str] = set()
        for t in tools:
            if t.key in candidate_keys:
                if not is_enabled(t):
                    continue
                try:
                    if await _tool_auth_allows(t):
                        existing_keys.add(t.key)
                except Exception:
                    existing_keys.add(t.key)

        synthetic: list[Tool] = []
        if list_tool.key not in existing_keys:
            synthetic.append(list_tool)
        if get_tool.key not in existing_keys:
            synthetic.append(get_tool)
        return [*tools, *synthetic]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name, including generated prompt tools."""
        from fastmcp.server.server import _tool_auth_allows
        from fastmcp.server.transforms.visibility import is_enabled

        if name not in ("list_prompts", "get_prompt"):
            return await call_next(name, version=version)

        syn = (
            self._make_list_prompts_tool()
            if name == "list_prompts"
            else self._make_get_prompt_tool()
        )
        syn_matches = version is None or version.matches(syn.version)

        existing = await call_next(name, version=version)
        if existing is not None and is_enabled(existing):
            try:
                allowed = await _tool_auth_allows(existing)
            except Exception:
                allowed = True
            if allowed:
                if syn_matches and self._is_tool_hidden_by_search(existing):
                    return syn
                return existing

        if syn_matches:
            return syn

        return None

    def _make_list_prompts_tool(self) -> Tool:
        """Create the list_prompts tool."""

        async def list_prompts() -> str:
            """List all available prompts.

            Returns JSON with prompt metadata including name, description,
            and optional arguments.
            """
            ctx = get_context()
            prompts = dedupe_with_versions(
                await ctx.fastmcp.list_prompts(), lambda p: p.name
            )

            result: list[dict[str, Any]] = []
            for p in prompts:
                result.append(  # noqa: PERF401
                    {
                        "name": p.name,
                        "description": p.description,
                        "arguments": [
                            {
                                "name": arg.name,
                                "description": arg.description,
                                "required": arg.required,
                            }
                            for arg in (p.arguments or [])
                        ],
                    }
                )

            return json.dumps(result, indent=2)

        return Tool.from_function(fn=list_prompts)

    def _make_get_prompt_tool(self) -> Tool:
        """Create the get_prompt tool."""

        async def get_prompt(
            name: Annotated[str, "The name of the prompt to get"],
            arguments: Annotated[
                dict[str, Any] | None,
                "Optional arguments for the prompt",
            ] = None,
        ) -> str | InputRequiredResult:
            """Get a prompt by name with optional arguments.

            Returns the rendered prompt as JSON with a messages array.
            Arguments should be provided as a dict mapping argument names
            to values.
            """
            ctx = get_context()
            result = await ctx.fastmcp.render_prompt(name, arguments=arguments or {})
            if isinstance(result, InputRequiredPromptResult):
                return result.input_required
            return _format_prompt_result(result)

        return Tool.from_function(fn=get_prompt)


def _format_prompt_result(result: Any) -> str:
    """Format PromptResult for tool output.

    Returns JSON with the messages array. Preserves embedded resources
    as structured JSON objects.
    """
    messages = []
    for msg in result.messages:
        if isinstance(msg.content, TextContent):
            content = msg.content.text
        else:
            content = msg.content.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )

        messages.append(
            {
                "role": msg.role,
                "content": content,
            }
        )

    return json.dumps({"messages": messages}, indent=2)
