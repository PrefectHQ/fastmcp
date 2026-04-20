"""Search plugin: catalog-search-as-a-plugin.

Wraps a `BaseSearchTransform` implementation (BM25 or regex) and
contributes it via the plugin `transforms()` hook. The transform
classes live in `.base`, `.bm25`, `.regex` as implementation detail;
user code should configure behavior through the plugin.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from fastmcp.server.plugins.base import Plugin, PluginMeta
from fastmcp.server.plugins.search.bm25 import BM25SearchTransform
from fastmcp.server.plugins.search.regex import RegexSearchTransform
from fastmcp.server.transforms import Transform


class SearchConfig(BaseModel):
    """Config model for the `Search` plugin."""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["bm25", "regex"] = "bm25"
    """Which matcher to use. BM25 ranks by relevance; regex filters by
    pattern match."""

    max_results: int = 5
    """Maximum tools returned per search."""

    always_visible: list[str] = []
    """Tool names that stay in `list_tools` alongside the synthetic
    search/call pair."""

    search_tool_name: str = "search_tools"
    """Name of the generated search tool."""

    call_tool_name: str = "call_tool"
    """Name of the generated call-tool proxy."""


class Search(Plugin[SearchConfig]):
    """Collapse the tool catalog behind a search interface.

    With the plugin active, `list_tools()` returns only a pinned set
    plus a generated `search_tools` / `call_tool` pair. Hidden tools
    remain callable — direct calls and the call-tool proxy both work.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.plugins.search import Search, SearchConfig

        # Default config:
        mcp = FastMCP("Server", plugins=[Search()])

        # Typed config (IDE completion + static validation):
        mcp = FastMCP(
            "Server",
            plugins=[Search(SearchConfig(strategy="regex", always_visible=["help"]))],
        )

        # Dict config (useful for loading from JSON/YAML):
        mcp = FastMCP("Server", plugins=[Search({"strategy": "regex"})])
        ```
    """

    meta = PluginMeta(
        name="search",
        version="0.1.0",
        description="Catalog search: replace list_tools output with a search interface.",
    )

    def transforms(self) -> list[Transform]:
        cls = (
            BM25SearchTransform if self.config.strategy == "bm25" else RegexSearchTransform
        )
        return [
            cls(
                max_results=self.config.max_results,
                always_visible=list(self.config.always_visible),
                search_tool_name=self.config.search_tool_name,
                call_tool_name=self.config.call_tool_name,
            )
        ]
