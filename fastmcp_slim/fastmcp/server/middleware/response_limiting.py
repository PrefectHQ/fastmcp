"""Response limiting middleware for controlling tool response sizes."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import mcp_types as mt
import pydantic_core
from mcp_types import TextContent

from fastmcp.tools.base import InputRequiredToolResult, Tool, ToolResult

from .middleware import CallNext, Middleware, MiddlewareContext

__all__ = ["ResponseLimitingMiddleware"]

logger = logging.getLogger(__name__)

# The middleware measures a ToolResult, but the client measures the
# CallToolResult that goes on the wire, whose envelope (annotations, isError,
# structuredContent) adds a fixed overhead beyond it. Measured against mcp
# 2.2.0: an empty text result is 126 bytes as ToolResult and 241 as
# CallToolResult. Reserve that envelope so the response the client measures
# also fits within max_size.
_WIRE_ENVELOPE_BYTES = 128


class ResponseLimitingMiddleware(Middleware):
    """Middleware that limits the response size of tool calls.

    Intercepts tool call responses and enforces size limits. If a response
    exceeds the limit, it extracts text content, truncates it, and returns
    a single TextContent block.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.middleware.response_limiting import (
            ResponseLimitingMiddleware,
        )

        mcp = FastMCP("MyServer")

        # Limit all tool responses to 500KB
        mcp.add_middleware(ResponseLimitingMiddleware(max_size=500_000))

        # Limit only specific tools
        mcp.add_middleware(
            ResponseLimitingMiddleware(
                max_size=100_000,
                tools=["search", "fetch_data"],
            )
        )
        ```
    """

    def __init__(
        self,
        *,
        max_size: int = 1_000_000,
        truncation_suffix: str = "\n\n[Response truncated due to size limit]",
        tools: list[str] | None = None,
    ) -> None:
        """Initialize response limiting middleware.

        Args:
            max_size: Maximum response size in bytes. Defaults to 1MB (1,000,000).
            truncation_suffix: Suffix to append when truncating responses.
                Defaults to "\\n\\n[Response truncated due to size limit]".
            tools: List of tool names to apply limiting to. If None, applies to all.
        """
        if max_size <= 0:
            raise ValueError(f"max_size must be positive, got {max_size}")
        self.max_size = max_size
        self.truncation_suffix = truncation_suffix
        self.tools = set(tools) if tools is not None else None

    def _limits_tool(self, name: str) -> bool:
        return self.tools is None or name in self.tools

    @staticmethod
    def _serialized_size(result: ToolResult) -> int:
        """Size of a result exactly as the middleware measures it elsewhere."""
        return len(pydantic_core.to_json(result, fallback=str))

    def _truncate_to_result(
        self,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Truncate text so the *serialized* result fits within max_size.

        The limit is checked on ``len(pydantic_core.to_json(result))``, so the
        budget has to be spent on that same measure: JSON escaping turns a
        control character into six bytes (``\u0001``) and a quote into two, and
        bounding the raw text length instead let escape-heavy responses come
        back several times larger than max_size.
        """

        def build(candidate: str) -> ToolResult:
            return ToolResult(
                content=[TextContent(type="text", text=candidate)],
                meta=meta,
            )

        budget = self.max_size - _WIRE_ENVELOPE_BYTES

        suffix_result = build(self.truncation_suffix)
        if self._serialized_size(suffix_result) > budget:
            # Edge case: the budget cannot hold even the truncation suffix.
            return suffix_result

        if self._serialized_size(build(text + self.truncation_suffix)) <= budget:
            return build(text + self.truncation_suffix)

        # Longest prefix whose serialized form (suffix included) still fits.
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if self._serialized_size(build(text[:mid] + self.truncation_suffix)) <= budget:
                low = mid
            else:
                high = mid - 1

        return build(text[:low] + self.truncation_suffix)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Hide schemas for tools whose response shape may be truncated to text."""
        tools = await call_next(context)
        return [
            tool.model_copy(update={"output_schema": None})
            if self._limits_tool(tool.name) and tool.output_schema is not None
            else tool
            for tool in tools
        ]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls and limit response size."""
        result = await call_next(context)

        # A multi-round-trip ask (SEP-2322) carries no tool content to measure,
        # and truncating it would collapse the InputRequiredToolResult into a
        # plain ToolResult — the wire handler would then serialize the ask as
        # content instead of returning it as an input-required result. Pass it
        # through untouched.
        if isinstance(result, InputRequiredToolResult):
            return result

        # A task-augmented call returns a CreateTaskResult (the tasks extension)
        # up through this middleware — a small acknowledgement with no tool
        # content to measure or truncate. Pass any non-ToolResult through.
        if not isinstance(result, ToolResult):
            return result

        # Check if we should limit this tool
        if not self._limits_tool(context.message.name):
            return result

        # Measure serialized size
        serialized = pydantic_core.to_json(result, fallback=str)
        if len(serialized) <= self.max_size:
            return result

        # Over limit: extract text, truncate, return single TextContent
        logger.warning(
            "Tool %r response exceeds size limit: %d bytes > %d bytes, truncating",
            context.message.name,
            len(serialized),
            self.max_size,
        )

        texts = [b.text for b in result.content if isinstance(b, TextContent)]
        text = (
            "\n\n".join(texts)
            if texts
            else serialized.decode("utf-8", errors="replace")
        )

        return self._truncate_to_result(text, meta=result.meta)
