"""Deprecation shim — search transforms moved to `fastmcp.server.plugins.tool_search`.

The preferred API is now the `ToolSearch` plugin:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.tool_search import ToolSearch

    mcp = FastMCP("Server", plugins=[ToolSearch()])

Transform classes remain importable from their new location
(`fastmcp.server.plugins.tool_search.{bm25,regex,base}`) for advanced
composition. This old path issues a `DeprecationWarning` on import.
"""

import warnings

from fastmcp.server.plugins.tool_search.base import (
    BaseSearchTransform,
    SearchResultSerializer,
    serialize_tools_for_output_json,
    serialize_tools_for_output_markdown,
)
from fastmcp.server.plugins.tool_search.bm25 import BM25SearchTransform
from fastmcp.server.plugins.tool_search.regex import RegexSearchTransform

warnings.warn(
    "fastmcp.server.transforms.search has moved to "
    "fastmcp.server.plugins.tool_search. Prefer the ToolSearch plugin: "
    "`from fastmcp.server.plugins.tool_search import ToolSearch`. The old "
    "import path will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "BM25SearchTransform",
    "BaseSearchTransform",
    "RegexSearchTransform",
    "SearchResultSerializer",
    "serialize_tools_for_output_json",
    "serialize_tools_for_output_markdown",
]
