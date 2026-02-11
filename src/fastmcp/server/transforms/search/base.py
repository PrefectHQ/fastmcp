"""Base class for search transforms.

Search transforms replace ``list_tools()`` output with a small set of
synthetic tools — a search tool and a call-tool proxy — so LLMs can
discover tools on demand instead of receiving the full catalog.

All concrete search transforms (``RegexSearchTransform``,
``BM25SearchTransform``, etc.) inherit from ``BaseSearchTransform`` and
implement ``_make_search_tool()`` and ``_search()`` to provide their
specific search strategy.

Example::

    from fastmcp import FastMCP
    from fastmcp.server.transforms.search import RegexSearchTransform

    mcp = FastMCP("Server")

    @mcp.tool
    def add(a: int, b: int) -> int: ...

    @mcp.tool
    def multiply(x: float, y: float) -> float: ...

    # Clients now see only ``search_tools`` and ``call_tool``.
    # The original tools are discoverable via search.
    mcp.add_transform(RegexSearchTransform())
"""

from abc import abstractmethod
from collections.abc import Sequence
from contextvars import ContextVar
from typing import Annotated, Any

from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec

# When True, search transforms pass through in ``list_tools()`` instead
# of hiding tools.  This lets the search tool call back into the server's
# ``list_tools()`` to get the auth-filtered catalog without recursively
# hiding everything behind the search interface.
_search_bypass: ContextVar[bool] = ContextVar("_search_bypass", default=False)


def _extract_searchable_text(tool: Tool) -> str:
    """Combine tool name, description, and parameter info into searchable text."""
    parts = [tool.name]
    if tool.description:
        parts.append(tool.description)

    schema = tool.parameters
    if schema:
        properties = schema.get("properties", {})
        for param_name, param_info in properties.items():
            parts.append(param_name)
            if isinstance(param_info, dict):
                desc = param_info.get("description", "")
                if desc:
                    parts.append(desc)

    return " ".join(parts)


def _serialize_tools_for_output(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Serialize tools to the same dict format as ``list_tools`` output."""
    return [
        tool.to_mcp_tool().model_dump(mode="json", exclude_none=True) for tool in tools
    ]


class BaseSearchTransform(Transform):
    """Replace the tool listing with a search interface.

    When this transform is active, ``list_tools()`` returns only:

    * Any tools listed in ``always_visible`` (pinned).
    * A **search tool** that finds tools matching a query.
    * A **call_tool** proxy that executes tools discovered via search.

    Hidden tools remain callable — ``get_tool()`` delegates unknown
    names downstream, so direct calls and the call-tool proxy both work.

    Search results respect the full auth pipeline: middleware, visibility
    transforms, and component-level auth checks all apply.

    Args:
        max_results: Maximum number of tools returned per search.
        always_visible: Tool names that stay in the ``list_tools``
            output alongside the synthetic search/call tools.
        search_tool_name: Name of the generated search tool.
        call_tool_name: Name of the generated call-tool proxy.
    """

    def __init__(
        self,
        *,
        max_results: int = 5,
        always_visible: list[str] | None = None,
        search_tool_name: str = "search_tools",
        call_tool_name: str = "call_tool",
    ) -> None:
        self._max_results = max_results
        self._always_visible = set(always_visible or [])
        self._search_tool_name = search_tool_name
        self._call_tool_name = call_tool_name

    # ------------------------------------------------------------------
    # Transform interface
    # ------------------------------------------------------------------

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Return only pinned + synthetic tools, or pass through if bypassed."""
        if _search_bypass.get():
            return tools

        pinned = [t for t in tools if t.name in self._always_visible]
        return [*pinned, self._make_search_tool(), self._make_call_tool()]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Intercept synthetic tool names; delegate everything else."""
        if name == self._search_tool_name:
            return self._make_search_tool()
        if name == self._call_tool_name:
            return self._make_call_tool()
        return await call_next(name, version=version)

    # ------------------------------------------------------------------
    # Synthetic tools
    # ------------------------------------------------------------------

    @abstractmethod
    def _make_search_tool(self) -> Tool:
        """Create the search tool. Subclasses define the parameter schema."""
        ...

    def _make_call_tool(self) -> Tool:
        """Create the call_tool proxy that executes discovered tools."""

        async def call_tool(
            name: Annotated[str, "The name of the tool to call"],
            arguments: Annotated[
                dict[str, Any] | None, "Arguments to pass to the tool"
            ] = None,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            """Call a tool by name with the given arguments.

            Use this to execute tools discovered via search_tools.
            """
            return await ctx.fastmcp.call_tool(name, arguments)

        return Tool.from_function(fn=call_tool, name=self._call_tool_name)

    # ------------------------------------------------------------------
    # Catalog access
    # ------------------------------------------------------------------

    async def _get_visible_tools(self, ctx: Context) -> Sequence[Tool]:
        """Get the auth-filtered tool catalog.

        Calls the server's ``list_tools()`` with a bypass flag so this
        transform passes through instead of hiding everything.  The rest
        of the pipeline — middleware, visibility, component auth — runs
        normally, so the result only contains tools the current user is
        authorized to see.
        """
        token = _search_bypass.set(True)
        try:
            tools = await ctx.fastmcp.list_tools()
        finally:
            _search_bypass.reset(token)
        return [t for t in tools if t.name not in self._always_visible]

    # ------------------------------------------------------------------
    # Abstract search
    # ------------------------------------------------------------------

    @abstractmethod
    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        """Search the given tools and return matches."""
        ...
