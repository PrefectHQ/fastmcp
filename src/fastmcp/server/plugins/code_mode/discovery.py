"""Discovery tool factories for the CodeMode plugin.

A discovery tool is a synthetic meta-tool the LLM uses to explore the real
tool catalog before calling anything. Each factory here is a callable
that receives catalog access (`GetToolCatalog`) and returns a ready-to-
publish `Tool`. They compose via the `discovery_tools` parameter on
`CodeMode`.

The four built-in factories cover the common discovery patterns:

* `Search` — query the catalog by text (BM25 by default).
* `GetSchemas` — fetch parameter schemas for a named list of tools.
* `GetTags` — browse the catalog grouped by tag.
* `ListTools` — dump every tool at a configurable detail level.

A typical progressive-disclosure setup pairs `Search` with `GetSchemas`:
the LLM searches to find candidates, then fetches schemas only for the
tools it actually plans to call.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, Literal

from fastmcp.server.context import Context
from fastmcp.server.plugins.tool_search.base import (
    serialize_tools_for_output_json,
    serialize_tools_for_output_markdown,
)
from fastmcp.tools.base import Tool

GetToolCatalog = Callable[[Context], Awaitable[Sequence[Tool]]]
"""Async callable that returns the auth-filtered tool catalog."""

SearchFn = Callable[[Sequence[Tool], str], Awaitable[Sequence[Tool]]]
"""Async callable that searches a tool sequence by query string."""

DiscoveryToolFactory = Callable[[GetToolCatalog], Tool]
"""Factory that receives catalog access and returns a synthetic Tool."""


ToolDetailLevel = Literal["brief", "detailed", "full"]
"""Detail level for discovery tool output.

- `"brief"`: tool names and one-line descriptions
- `"detailed"`: compact markdown with parameter names, types, and required markers
- `"full"`: complete JSON schema
"""


def _render_tools(tools: Sequence[Tool], detail: ToolDetailLevel) -> str:
    """Render tools at the requested detail level.

    The same detail value produces the same output format regardless of
    which discovery tool calls this, so `detail="detailed"` on Search
    gives identical formatting to `detail="detailed"` on GetSchemas.
    """
    if not tools:
        if detail == "full":
            return json.dumps([], indent=2)
        return "No tools matched the query."
    if detail == "full":
        return json.dumps(serialize_tools_for_output_json(tools), indent=2)
    if detail == "detailed":
        return serialize_tools_for_output_markdown(tools)
    # brief
    lines: list[str] = []
    for tool in tools:
        desc = f": {tool.description}" if tool.description else ""
        lines.append(f"- {tool.name}{desc}")
    return "\n".join(lines)


class Search:
    """Discovery tool factory that searches the catalog by query.

    Args:
        search_fn: Async callable `(tools, query) -> matching_tools`.
            Defaults to BM25 ranking.
        name: Name of the synthetic tool exposed to the LLM.
        default_detail: Default detail level for search results.
            `"brief"` returns tool names and descriptions only.
            `"detailed"` returns compact markdown with parameter schemas.
            `"full"` returns complete JSON tool definitions.
        default_limit: Maximum number of results to return. The LLM can
            override this per call. `None` means no limit.
    """

    def __init__(
        self,
        *,
        search_fn: SearchFn | None = None,
        name: str = "search",
        default_detail: ToolDetailLevel | None = None,
        default_limit: int | None = None,
    ) -> None:
        if search_fn is None:
            from fastmcp.server.plugins.tool_search.bm25 import BM25SearchTransform

            _bm25 = BM25SearchTransform(max_results=default_limit or 50)
            search_fn = _bm25._search
        self._search_fn = search_fn
        self._name = name
        self._default_detail: ToolDetailLevel = default_detail or "brief"
        self._default_limit = default_limit

    def __call__(self, get_catalog: GetToolCatalog) -> Tool:
        search_fn = self._search_fn
        default_detail = self._default_detail
        default_limit = self._default_limit

        async def search(
            query: Annotated[str, "Search query to find available tools"],
            tags: Annotated[
                list[str] | None,
                "Filter to tools with any of these tags before searching",
            ] = None,
            detail: Annotated[
                ToolDetailLevel,
                "'brief' for names and descriptions, 'detailed' for parameter schemas as markdown, 'full' for complete JSON schemas",
            ] = default_detail,
            limit: Annotated[
                int | None,
                "Maximum number of results to return",
            ] = default_limit,
            ctx: Context = None,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        ) -> str:
            """Search for available tools by query.

            Returns matching tools ranked by relevance.
            """
            catalog = await get_catalog(ctx)
            catalog_size = len(catalog)
            tools: Sequence[Tool] = catalog
            if tags:
                tag_set = set(tags)
                has_untagged = "untagged" in tag_set
                real_tags = tag_set - {"untagged"}
                tools = [
                    t
                    for t in tools
                    if (t.tags & real_tags) or (has_untagged and not t.tags)
                ]
            results = await search_fn(tools, query)
            if limit is not None:
                results = results[:limit]
            rendered = _render_tools(results, detail)
            if len(results) < catalog_size and detail != "full":
                n = len(results)
                rendered = f"{n} of {catalog_size} tools:\n\n{rendered}"
            return rendered

        return Tool.from_function(fn=search, name=self._name)


class GetSchemas:
    """Discovery tool factory that returns schemas for tools by name.

    Args:
        name: Name of the synthetic tool exposed to the LLM.
        default_detail: Default detail level for schema results.
            `"brief"` returns tool names and descriptions only.
            `"detailed"` renders compact markdown with parameter names,
            types, and required markers.
            `"full"` returns the complete JSON schema.
    """

    def __init__(
        self,
        *,
        name: str = "get_schema",
        default_detail: ToolDetailLevel | None = None,
    ) -> None:
        self._name = name
        self._default_detail: ToolDetailLevel = default_detail or "detailed"

    def __call__(self, get_catalog: GetToolCatalog) -> Tool:
        default_detail = self._default_detail

        async def get_schema(
            tools: Annotated[
                list[str],
                "List of tool names to get schemas for",
            ],
            detail: Annotated[
                ToolDetailLevel,
                "'brief' for names and descriptions, 'detailed' for parameter schemas as markdown, 'full' for complete JSON schemas",
            ] = default_detail,
            ctx: Context = None,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        ) -> str:
            """Get parameter schemas for specific tools.

            Use after searching to get the detail needed to call a tool.
            """
            catalog = await get_catalog(ctx)
            catalog_by_name = {t.name: t for t in catalog}
            matched = [catalog_by_name[n] for n in tools if n in catalog_by_name]
            not_found = [n for n in tools if n not in catalog_by_name]

            if not matched and not_found:
                return f"Tools not found: {', '.join(not_found)}"

            if detail == "full":
                data = serialize_tools_for_output_json(matched)
                if not_found:
                    data.append({"not_found": not_found})
                return json.dumps(data, indent=2)

            result = _render_tools(matched, detail)
            if not_found:
                result += f"\n\nTools not found: {', '.join(not_found)}"
            return result

        return Tool.from_function(fn=get_schema, name=self._name)


class GetTags:
    """Discovery tool factory that lists tool tags from the catalog.

    Reads `tool.tags` from the catalog and groups tools by tag. Tools
    without tags appear under `"untagged"`.

    Args:
        name: Name of the synthetic tool exposed to the LLM.
        default_detail: Default detail level.
            `"brief"` returns tag names with tool counts.
            `"full"` lists all tools under each tag.
    """

    def __init__(
        self,
        *,
        name: str = "tags",
        default_detail: Literal["brief", "full"] | None = None,
    ) -> None:
        self._name = name
        self._default_detail: Literal["brief", "full"] = default_detail or "brief"

    def __call__(self, get_catalog: GetToolCatalog) -> Tool:
        default_detail = self._default_detail

        async def tags(
            detail: Annotated[
                Literal["brief", "full"],
                "Level of detail: 'brief' for tag names and counts, 'full' for tools listed under each tag",
            ] = default_detail,
            ctx: Context = None,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        ) -> str:
            """List available tool tags.

            Use to browse available tools by tag before searching.
            """
            catalog = await get_catalog(ctx)
            by_tag: dict[str, list[Tool]] = {}
            for tool in catalog:
                if tool.tags:
                    for tag in tool.tags:
                        by_tag.setdefault(tag, []).append(tool)
                else:
                    by_tag.setdefault("untagged", []).append(tool)

            if not by_tag:
                return "No tools available."

            if detail == "brief":
                lines = [
                    f"- {tag} ({len(tools)} tool{'s' if len(tools) != 1 else ''})"
                    for tag, tools in sorted(by_tag.items())
                ]
                return "\n".join(lines)

            blocks: list[str] = []
            for tag, tools in sorted(by_tag.items()):
                lines = [f"### {tag}"]
                for tool in tools:
                    desc = f": {tool.description}" if tool.description else ""
                    lines.append(f"- {tool.name}{desc}")
                blocks.append("\n".join(lines))
            return "\n\n".join(blocks)

        return Tool.from_function(fn=tags, name=self._name)


class ListTools:
    """Discovery tool factory that lists all tools in the catalog.

    Args:
        name: Name of the synthetic tool exposed to the LLM.
        default_detail: Default detail level.
            `"brief"` returns tool names and one-line descriptions.
            `"detailed"` returns compact markdown with parameter schemas.
            `"full"` returns the complete JSON schema.
    """

    def __init__(
        self,
        *,
        name: str = "list_tools",
        default_detail: ToolDetailLevel | None = None,
    ) -> None:
        self._name = name
        self._default_detail: ToolDetailLevel = default_detail or "brief"

    def __call__(self, get_catalog: GetToolCatalog) -> Tool:
        default_detail = self._default_detail

        async def list_tools(
            detail: Annotated[
                ToolDetailLevel,
                "'brief' for names and descriptions, 'detailed' for parameter schemas as markdown, 'full' for complete JSON schemas",
            ] = default_detail,
            ctx: Context = None,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        ) -> str:
            """List all available tools.

            Use to see the full catalog before searching or calling tools.
            """
            catalog = await get_catalog(ctx)
            return _render_tools(catalog, detail)

        return Tool.from_function(fn=list_tools, name=self._name)
