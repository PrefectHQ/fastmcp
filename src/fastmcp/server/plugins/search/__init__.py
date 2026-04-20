"""Search plugin — replace the tool catalog with a search interface.

The `Search` plugin is the public entry point:

    from fastmcp import FastMCP
    from fastmcp.server.plugins import Search

    mcp = FastMCP("Server", plugins=[Search()])

Transform classes (`BM25SearchTransform`, `RegexSearchTransform`,
`BaseSearchTransform`) are implementation detail but remain importable
from `fastmcp.server.plugins.search.{bm25,regex,base}` for advanced
composition (custom transform stacks).
"""

from fastmcp.server.plugins.search.base import (
    BaseSearchTransform,
    SearchResultSerializer,
    serialize_tools_for_output_json,
    serialize_tools_for_output_markdown,
)
from fastmcp.server.plugins.search.bm25 import BM25SearchTransform
from fastmcp.server.plugins.search.regex import RegexSearchTransform
from fastmcp.server.plugins.search.plugin import Search, SearchConfig

__all__ = [
    "Search",
    "SearchConfig",
    "BaseSearchTransform",
    "BM25SearchTransform",
    "RegexSearchTransform",
    "SearchResultSerializer",
    "serialize_tools_for_output_json",
    "serialize_tools_for_output_markdown",
]
