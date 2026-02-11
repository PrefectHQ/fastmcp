"""Response limiting middleware for controlling tool response sizes."""

from __future__ import annotations

import logging

import mcp.types as mt
import pydantic_core
from mcp.types import TextContent

from fastmcp.tools.tool import ToolResult

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

    def _truncate_to_result(self, text: str) -> ToolResult:
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

        return ToolResult(content=[TextContent(type="text", text=truncated)])

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls and limit response size."""
        result = await call_next(context)

        # Check if we should limit this tool
        if self.tools is not None and context.message.name not in self.tools:
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

        return self._truncate_to_result(text)
