"""BM25-based search transform."""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Annotated, Any

from fastmcp.server.context import Context
from fastmcp.server.transforms.search.base import (
    BaseSearchTransform,
    _extract_searchable_text,
    _serialize_tools_for_output,
)
from fastmcp.tools.tool import Tool


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, filter short tokens."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1]


class _BM25Index:
    """Self-contained BM25 Okapi index."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._doc_tokens: list[list[str]] = []
        self._doc_lengths: list[int] = []
        self._avg_dl: float = 0.0
        self._df: dict[str, int] = {}
        self._tf: list[dict[str, int]] = []
        self._n: int = 0

    def build(self, documents: list[str]) -> None:
        self._doc_tokens = [_tokenize(doc) for doc in documents]
        self._doc_lengths = [len(tokens) for tokens in self._doc_tokens]
        self._n = len(documents)
        self._avg_dl = sum(self._doc_lengths) / self._n if self._n else 0.0

        self._df = {}
        self._tf = []
        for tokens in self._doc_tokens:
            tf: dict[str, int] = {}
            seen: set[str] = set()
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
                if token not in seen:
                    self._df[token] = self._df.get(token, 0) + 1
                    seen.add(token)
            self._tf.append(tf)

    def query(self, text: str, top_k: int) -> list[int]:
        """Return indices of top_k documents sorted by BM25 score."""
        query_tokens = _tokenize(text)
        if not query_tokens or not self._n:
            return []

        scores: list[float] = [0.0] * self._n
        for token in query_tokens:
            if token not in self._df:
                continue
            idf = math.log(
                (self._n - self._df[token] + 0.5) / (self._df[token] + 0.5) + 1.0
            )
            for i in range(self._n):
                tf = self._tf[i].get(token, 0)
                if tf == 0:
                    continue
                dl = self._doc_lengths[i]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self._avg_dl)
                scores[i] += idf * numerator / denominator

        ranked = sorted(range(self._n), key=lambda i: scores[i], reverse=True)
        return [i for i in ranked[:top_k] if scores[i] > 0]


def _catalog_hash(tools: Sequence[Tool]) -> str:
    """SHA256 hash of sorted tool searchable text for staleness detection."""
    key = "|".join(sorted(_extract_searchable_text(t) for t in tools))
    return hashlib.sha256(key.encode()).hexdigest()


class BM25SearchTransform(BaseSearchTransform):
    """Search transform using BM25 Okapi relevance ranking.

    Maintains an in-memory index that is lazily rebuilt when the tool
    catalog changes (detected via a hash of tool names).
    """

    def __init__(
        self,
        *,
        max_results: int = 5,
        always_visible: list[str] | None = None,
        search_tool_name: str = "search_tools",
        call_tool_name: str = "call_tool",
    ) -> None:
        super().__init__(
            max_results=max_results,
            always_visible=always_visible,
            search_tool_name=search_tool_name,
            call_tool_name=call_tool_name,
        )
        self._index = _BM25Index()
        self._indexed_tools: Sequence[Tool] = ()
        self._last_hash: str = ""

    def _make_search_tool(self) -> Tool:
        transform = self

        async def search_tools(
            query: Annotated[str, "Natural language query to search for tools"],
            ctx: Context = None,  # type: ignore[assignment]
        ) -> list[dict[str, Any]]:
            """Search for tools using natural language.

            Returns matching tool definitions ranked by relevance,
            in the same format as list_tools.
            """
            hidden = await transform._get_visible_tools(ctx)
            results = await transform._search(hidden, query)
            return _serialize_tools_for_output(results)

        return Tool.from_function(fn=search_tools, name=self._search_tool_name)

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        current_hash = _catalog_hash(tools)
        if current_hash != self._last_hash:
            documents = [_extract_searchable_text(t) for t in tools]
            new_index = _BM25Index(self._index.k1, self._index.b)
            new_index.build(documents)
            self._index, self._indexed_tools, self._last_hash = (
                new_index,
                tools,
                current_hash,
            )

        indices = self._index.query(query, self._max_results)
        return [self._indexed_tools[i] for i in indices]
