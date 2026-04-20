"""Tool-search plugin — replace the tool catalog with a search interface.

The `ToolSearch` plugin is the public entry point:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.tool_search import ToolSearch

    mcp = FastMCP("Server", plugins=[ToolSearch()])

Transform classes (`BM25SearchTransform`, `RegexSearchTransform`,
`BaseSearchTransform`) live in `.bm25`, `.regex`, `.base` submodules
for advanced composition (custom transform stacks) but are not
re-exported here — import from the submodule path when needed.
"""

from fastmcp.server.plugins.tool_search.plugin import ToolSearch, ToolSearchConfig

__all__ = ["ToolSearch", "ToolSearchConfig"]
