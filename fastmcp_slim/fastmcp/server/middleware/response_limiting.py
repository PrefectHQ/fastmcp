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

    def _truncate_to_result(
        self,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Truncate text to fit within max_size and wrap in ToolResult."""
        suffix_bytes = len(self.truncation_suffix.encode("utf-8"))
        # Account for JSON wrapper overhead: {"content":[{"type":"text","text":"..."}]}
        overhead = 50
        target_size = self.max_size - suffix_bytes - overhead

        if target_size <= 0:
            # Edge case: max_size too small for even the suffix
            truncated = self.truncation_suffix
        else:
            # Truncate to target size, preserving UTF-8 boundaries
            encoded = text.encode("utf-8")
            if len(encoded) <= target_size:
                truncated = text + self.truncation_suffix
            else:
                truncated = (
                    encoded[:target_size].decode("utf-8", errors="ignore")
                    + self.truncation_suffix
                )

        return ToolResult(
            content=[TextContent(type="text", text=truncated)],
            meta=meta,
        )

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
