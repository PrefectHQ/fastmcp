"""Regex-based search transform."""

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


class RegexSearchTransform(BaseSearchTransform):
    """Search transform using regex pattern matching.

    Tools are matched against their name, description, and parameter
    information using ``re.search`` with ``re.IGNORECASE``.
    """

    def _make_search_tool(self) -> Tool:
        transform = self

        async def search_tools(
            pattern: Annotated[
                str,
                "Regex pattern to match against tool names, descriptions, and parameters",
            ],
            ctx: Context = None,  # type: ignore[assignment]
        ) -> list[dict[str, Any]]:
            """Search for tools matching a regex pattern.

            Returns matching tool definitions in the same format as list_tools.
            """
            hidden = await transform._get_visible_tools(ctx)
            results = await transform._search(hidden, pattern)
            return _serialize_tools_for_output(results)

        return Tool.from_function(fn=search_tools, name=self._search_tool_name)

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        try:
            compiled = re.compile(query, re.IGNORECASE)
        except re.error:
            return []

        matches: list[Tool] = []
        for tool in tools:
            text = _extract_searchable_text(tool)
            if compiled.search(text):
                matches.append(tool)
                if len(matches) >= self._max_results:
                    break
        return matches
