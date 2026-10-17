"""Regex-based search transform."""

import time
from collections.abc import Sequence
from typing import Annotated, Any

from pydantic_core import SchemaError, SchemaValidator, ValidationError, core_schema

from fastmcp.server.context import Context
from fastmcp.server.transforms.search.base import (
    BaseSearchTransform,
    _extract_searchable_text,
)
from fastmcp.tools.base import Tool
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class RegexSearchTransform(BaseSearchTransform):
    """Search transform using regex pattern matching.

    Tools are matched against their name, description, and parameter
    information using Pydantic's Rust regex engine with case-insensitive
    matching. Lookarounds and backreferences are not supported.

    A search stops after about a second and returns the matches found so
    far, so a costly pattern cannot hold the worker for long.
    """

    _max_pattern_length = 1000
    _search_time_budget = 1.0

    def _make_search_tool(self) -> Tool:
        transform = self

        async def search_tools(
            pattern: Annotated[
                str,
                "Case-insensitive regex pattern for tool names, descriptions, and parameters, "
                "up to 1000 characters; lookarounds and backreferences are not supported",
            ],
            ctx: Context = None,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        ) -> str | list[dict[str, Any]]:
            """Search for tools matching a regex pattern.

            Returns matching tool definitions in the same format as list_tools.
            Invalid, unsupported, or overlong patterns return an empty list.
            """
            hidden = await transform._get_visible_tools(ctx)
            results = await transform._search(hidden, pattern)
            return await transform._render_results(results)

        return Tool.from_function(fn=search_tools, name=self._search_tool_name)

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        if len(query) > self._max_pattern_length:
            return []
        deadline = time.monotonic() + self._search_time_budget
        try:
            matcher = SchemaValidator(
                core_schema.str_schema(
                    pattern=f"(?i){query}", regex_engine="rust-regex"
                )
            )
        except SchemaError:
            return []

        matches: list[Tool] = []
        for tool in tools:
            if time.monotonic() > deadline:
                logger.warning(
                    "Regex tool search exceeded %ss; returning %d partial matches",
                    self._search_time_budget,
                    len(matches),
                )
                break
            text = _extract_searchable_text(tool)
            try:
                matcher.validate_python(text)
            except ValidationError:
                continue
            matches.append(tool)
            if len(matches) >= self._max_results:
                break
        return matches
