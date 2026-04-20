"""Deprecation shim — search transforms moved to `fastmcp.server.plugins.search`.

The preferred API is now the `Search` plugin:

    from fastmcp import FastMCP
    from fastmcp.server.plugins import Search

    mcp = FastMCP("Server", plugins=[Search()])

Transform classes remain importable from their new location
(`fastmcp.server.plugins.search.{bm25,regex,base}`) for advanced
composition. This old path issues a `DeprecationWarning` on import.
"""

import warnings

from fastmcp.server.plugins.search.base import (
    BaseSearchTransform,
    SearchResultSerializer,
    serialize_tools_for_output_json,
    serialize_tools_for_output_markdown,
)
from fastmcp.server.plugins.search.bm25 import BM25SearchTransform
from fastmcp.server.plugins.search.regex import RegexSearchTransform

warnings.warn(
    "fastmcp.server.transforms.search has moved to "
    "fastmcp.server.plugins.search. Prefer the Search plugin: "
    "`from fastmcp.server.plugins import Search`. The old import path "
    "will be removed in a future release.",
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
