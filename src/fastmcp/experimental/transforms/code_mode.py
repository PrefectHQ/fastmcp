import asyncio
import importlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, Literal, Protocol

from mcp.types import TextContent
from pydantic import Field

from fastmcp.exceptions import NotFoundError
from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.server.transforms.search.base import (
    serialize_tools_for_output_json,
    serialize_tools_for_output_markdown,
)
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

GetToolCatalog = Callable[[Context], Awaitable[Sequence[Tool]]]
"""Async callable that returns the auth-filtered tool catalog."""

SearchFn = Callable[[Sequence[Tool], str], Awaitable[Sequence[Tool]]]
"""Async callable that searches a tool sequence by query string."""

DiscoveryToolFactory = Callable[[GetToolCatalog], Tool]
"""Factory that receives catalog access and returns a synthetic Tool."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_async(fn: Callable[..., Any]) -> Callable[..., Any]:
    if asyncio.iscoroutinefunction(fn):
        return fn

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


def _unwrap_tool_result(result: ToolResult) -> dict[str, Any] | str:
    """Convert a ToolResult for use in the sandbox.

    - Output schema present → structured_content dict (matches the schema)
    - Otherwise → concatenated text content as a string
    """
    if result.structured_content is not None:
        return result.structured_content

    parts: list[str] = []
    for content in result.content:
        if isinstance(content, TextContent):
            parts.append(content.text)
        else:
            parts.append(str(content))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sandbox providers
# ---------------------------------------------------------------------------


class SandboxProvider(Protocol):
    """Interface for executing LLM-generated Python code in a sandbox.

    WARNING: The ``code`` parameter passed to ``run`` contains untrusted,
    LLM-generated Python.  Implementations MUST execute it in an isolated
    sandbox — never with plain ``exec()``.  Use ``MontySandboxProvider``
    (backed by ``pydantic-monty``) for production workloads.
    """

    async def run(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Callable[..., Any]] | None = None,
    ) -> Any: ...


class MontySandboxProvider:
    """Sandbox provider backed by `pydantic-monty`.

    Args:
        limits: Resource limits for sandbox execution. Supported keys:
            ``max_duration_secs`` (float), ``max_allocations`` (int),
            ``max_memory`` (int), ``max_recursion_depth`` (int),
            ``gc_interval`` (int).  All are optional; omit a key to
            leave that limit uncapped.
    """

    def __init__(
        self,
        *,
        limits: dict[str, Any] | None = None,
    ) -> None:
        self.limits = limits

    async def run(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Callable[..., Any]] | None = None,
    ) -> Any:
        try:
            pydantic_monty = importlib.import_module("pydantic_monty")
        except ModuleNotFoundError as exc:
            raise ImportError(
                "CodeMode requires pydantic-monty for the Monty sandbox provider. "
                "Install it with `fastmcp[code-mode]` or pass a custom SandboxProvider."
            ) from exc

        inputs = inputs or {}
        async_functions = {
            key: _ensure_async(value)
            for key, value in (external_functions or {}).items()
        }

        monty = pydantic_monty.Monty(
            code,
            inputs=list(inputs.keys()),
            external_functions=list(async_functions.keys()),
        )
        run_kwargs: dict[str, Any] = {"external_functions": async_functions}
        if inputs:
            run_kwargs["inputs"] = inputs
        if self.limits is not None:
            run_kwargs["limits"] = self.limits
        return await pydantic_monty.run_monty_async(monty, **run_kwargs)


# ---------------------------------------------------------------------------
# Built-in discovery tools
# ---------------------------------------------------------------------------


def _render_brief(tools: Sequence[Tool]) -> str:
    """Render tools as a lightweight name + description list."""
    lines: list[str] = []
    for tool in tools:
        desc = f": {tool.description}" if tool.description else ""
        lines.append(f"- {tool.name}{desc}")
    return "\n".join(lines) if lines else "No tools matched the query."


def _render_detail(tools: Sequence[Tool], detail: str) -> str:
    """Render tools at the requested detail level."""
    if detail == "full":
        return json.dumps(serialize_tools_for_output_json(tools), indent=2)
    return serialize_tools_for_output_markdown(tools)


class Search:
    """Discovery tool factory that searches the catalog by query.

    Args:
        search_fn: Async callable ``(tools, query) -> matching_tools``.
            Defaults to BM25 ranking.
        name: Name of the synthetic tool exposed to the LLM.
        default_detail: Default detail level for search results.
            ``"brief"`` returns tool names and descriptions only.
            ``"full"`` returns complete JSON tool definitions.
    """

    def __init__(
        self,
        *,
        search_fn: SearchFn | None = None,
        name: str = "search",
        default_detail: Literal["brief", "full"] = "brief",
    ) -> None:
        if search_fn is None:
            from fastmcp.server.transforms.search.bm25 import BM25SearchTransform

            _bm25 = BM25SearchTransform()
            search_fn = _bm25._search
        self._search_fn = search_fn
        self._name = name
        self._default_detail = default_detail

    def __call__(self, get_catalog: GetToolCatalog) -> Tool:
        search_fn = self._search_fn
        default_detail = self._default_detail

        async def search(
            query: Annotated[str, "Search query to find available tools"],
            tags: Annotated[
                list[str] | None,
                "Filter to tools with any of these tags before searching",
            ] = None,
            detail: Annotated[
                Literal["brief", "full"],
                "Level of detail in results: 'brief' for names and descriptions, 'full' for complete schemas",
            ] = default_detail,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> str:
            """Search for available tools by query.

            Returns matching tools ranked by relevance.
            """
            tools = await get_catalog(ctx)
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
            if detail == "brief":
                return _render_brief(results)
            return _render_detail(results, detail)

        return Tool.from_function(fn=search, name=self._name)


class GetSchemas:
    """Discovery tool factory that returns schemas for tools by name.

    Args:
        name: Name of the synthetic tool exposed to the LLM.
        default_detail: Default detail level for schema results.
            ``"brief"`` renders compact markdown with parameter names,
            types, and required markers.
            ``"full"`` returns the complete JSON schema.
    """

    def __init__(
        self,
        *,
        name: str = "get_schema",
        default_detail: Literal["brief", "full"] = "brief",
    ) -> None:
        self._name = name
        self._default_detail = default_detail

    def __call__(self, get_catalog: GetToolCatalog) -> Tool:
        default_detail = self._default_detail

        async def get_schema(
            tools: Annotated[
                list[str],
                "List of tool names to get schemas for",
            ],
            detail: Annotated[
                Literal["brief", "full"],
                "Level of detail: 'brief' for compact markdown, 'full' for complete JSON schema",
            ] = default_detail,
            ctx: Context = None,  # type: ignore[assignment]
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

            result = serialize_tools_for_output_markdown(matched)
            if not_found:
                result += f"\n\nTools not found: {', '.join(not_found)}"
            return result

        return Tool.from_function(fn=get_schema, name=self._name)


class Tags:
    """Discovery tool factory that lists tool tags from the catalog.

    Reads ``tool.tags`` from the catalog and groups tools by tag. Tools
    without tags appear under ``"untagged"``.

    Args:
        name: Name of the synthetic tool exposed to the LLM.
        default_detail: Default detail level.
            ``"brief"`` returns tag names with tool counts.
            ``"full"`` lists all tools under each tag.
    """

    def __init__(
        self,
        *,
        name: str = "tags",
        default_detail: Literal["brief", "full"] = "brief",
    ) -> None:
        self._name = name
        self._default_detail = default_detail

    def __call__(self, get_catalog: GetToolCatalog) -> Tool:
        default_detail = self._default_detail

        async def tags(
            detail: Annotated[
                Literal["brief", "full"],
                "Level of detail: 'brief' for tag names and counts, 'full' for tools listed under each tag",
            ] = default_detail,
            ctx: Context = None,  # type: ignore[assignment]
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


# ---------------------------------------------------------------------------
# CodeMode
# ---------------------------------------------------------------------------


def _default_discovery_tools() -> list[DiscoveryToolFactory]:
    return [Search(), GetSchemas()]


class CodeMode(CatalogTransform):
    """Transform that collapses all tools into discovery + execute meta-tools.

    Discovery tools are composable via the ``discovery_tools`` parameter.
    Each is a callable that receives catalog access and returns a ``Tool``.
    By default, ``Search`` and ``GetSchemas`` are included for
    progressive disclosure: search finds candidates, get_schema retrieves
    parameter details, and execute runs code.

    The ``execute`` tool is always present and provides a sandboxed Python
    environment with ``call_tool(name, params)`` in scope.
    """

    def __init__(
        self,
        *,
        default_arguments: dict[str, Any] | None = None,
        sandbox_provider: SandboxProvider | None = None,
        discovery_tools: list[DiscoveryToolFactory] | None = None,
        execute_tool_name: str = "execute",
        execute_description: str | None = None,
    ) -> None:
        super().__init__()
        self._default_arguments = default_arguments or {}
        self.execute_tool_name = execute_tool_name
        self.execute_description = execute_description
        self.sandbox_provider = sandbox_provider or MontySandboxProvider()

        self._discovery_factories = (
            discovery_tools
            if discovery_tools is not None
            else _default_discovery_tools()
        )
        self._built_discovery_tools: list[Tool] | None = None
        self._cached_execute_tool: Tool | None = None

    def _build_discovery_tools(self) -> list[Tool]:
        if self._built_discovery_tools is None:
            tools = [
                factory(self.get_tool_catalog) for factory in self._discovery_factories
            ]
            names = {t.name for t in tools}
            if self.execute_tool_name in names:
                raise ValueError(
                    f"Discovery tool name '{self.execute_tool_name}' "
                    f"collides with execute_tool_name."
                )
            if len(names) != len(tools):
                raise ValueError("Discovery tools must have unique names.")
            self._built_discovery_tools = tools
        return self._built_discovery_tools

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [*self._build_discovery_tools(), self._get_execute_tool()]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        for tool in self._build_discovery_tools():
            if tool.name == name:
                return tool
        if name == self.execute_tool_name:
            return self._get_execute_tool()
        return await call_next(name, version=version)

    def _build_execute_description(self) -> str:
        if self.execute_description is not None:
            return self.execute_description

        return (
            "Chain `await call_tool(...)` calls in one Python block; prefer returning the final answer from a single block.\n"
            "Use `return` to produce output.\n"
            "Only `call_tool(tool_name: str, params: dict) -> Any` is available in scope."
        )

    @staticmethod
    def _find_tool(name: str, tools: Sequence[Tool]) -> Tool | None:
        """Find a tool by name from a pre-fetched list."""
        for tool in tools:
            if tool.name == name:
                return tool
        return None

    def _get_execute_tool(self) -> Tool:
        if self._cached_execute_tool is None:
            self._cached_execute_tool = self._make_execute_tool()
        return self._cached_execute_tool

    def _make_execute_tool(self) -> Tool:
        transform = self

        async def execute(
            code: Annotated[
                str,
                Field(
                    description=(
                        "Python async code to execute tool calls via call_tool(name, arguments)"
                    )
                ),
            ],
            ctx: Context = None,  # type: ignore[assignment]
        ) -> Any:
            """Execute tool calls using Python code."""
            defaults = transform._default_arguments
            cached_tools: Sequence[Tool] | None = None

            async def _get_cached_tools() -> Sequence[Tool]:
                nonlocal cached_tools
                if cached_tools is None:
                    cached_tools = await transform.get_tool_catalog(ctx)
                return cached_tools

            async def call_tool(tool_name: str, params: dict[str, Any]) -> Any:
                backend_tools = await _get_cached_tools()
                tool = transform._find_tool(tool_name, backend_tools)
                if tool is None:
                    raise NotFoundError(f"Unknown tool: {tool_name}")

                accepted_args = set(tool.parameters.get("properties", {}).keys())
                skipped = {
                    key
                    for key in defaults
                    if key not in params and key not in accepted_args
                }
                if skipped:
                    logger.debug(
                        "default_arguments keys %s not accepted by tool %r, skipping",
                        skipped,
                        tool.name,
                    )
                merged = {
                    key: value
                    for key, value in defaults.items()
                    if key not in params and key in accepted_args
                }
                merged.update(params)

                result = await ctx.fastmcp.call_tool(tool.name, merged)
                return _unwrap_tool_result(result)

            return await transform.sandbox_provider.run(
                code,
                external_functions={"call_tool": call_tool},
            )

        return Tool.from_function(
            fn=execute,
            name=self.execute_tool_name,
            description=self._build_execute_description(),
        )


__all__ = [
    "CodeMode",
    "DiscoveryToolFactory",
    "GetSchemas",
    "GetToolCatalog",
    "MontySandboxProvider",
    "SandboxProvider",
    "Search",
    "SearchFn",
    "Tags",
    "serialize_tools_for_output_json",
    "serialize_tools_for_output_markdown",
]
