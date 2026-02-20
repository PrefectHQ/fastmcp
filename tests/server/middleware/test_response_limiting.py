"""Tests for ResponseLimitingMiddleware."""

import pytest
from mcp.types import ImageContent, TextContent

from fastmcp import Client, FastMCP
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.tools.tool import ToolResult


class TestResponseLimitingMiddleware:
    """Tests for ResponseLimitingMiddleware."""

    @pytest.fixture
    def mcp_server(self) -> FastMCP:
        """Create a basic MCP server for testing."""
        return FastMCP("test-server")

    async def test_response_under_limit_passes_unchanged(self, mcp_server: FastMCP):
        """Test that responses under the limit pass through unchanged."""
        mcp_server.add_middleware(ResponseLimitingMiddleware(max_size=1_000_000))

        @mcp_server.tool()
        def small_tool() -> ToolResult:
            return ToolResult(content=[TextContent(type="text", text="hello world")])

        async with Client(mcp_server) as client:
            result = await client.call_tool("small_tool", {})
            assert len(result.content) == 1
            assert result.content[0].text == "hello world"

    async def test_response_over_limit_is_truncated(self, mcp_server: FastMCP):
        """Test that responses over the limit are truncated."""
        mcp_server.add_middleware(ResponseLimitingMiddleware(max_size=500))

        @mcp_server.tool()
        def large_tool() -> ToolResult:
            return ToolResult(content=[TextContent(type="text", text="x" * 10_000)])

        async with Client(mcp_server) as client:
            result = await client.call_tool("large_tool", {})
            assert len(result.content) == 1
            assert "[Response truncated due to size limit]" in result.content[0].text
            # Verify truncated result fits within limit
            assert len(result.content[0].text.encode("utf-8")) < 500

    async def test_tool_filtering(self, mcp_server: FastMCP):
        """Test that tool filtering only applies to specified tools."""
        mcp_server.add_middleware(
            ResponseLimitingMiddleware(max_size=100, tools=["limited_tool"])
        )

        @mcp_server.tool()
        def limited_tool() -> ToolResult:
            return ToolResult(content=[TextContent(type="text", text="x" * 10_000)])

        @mcp_server.tool()
        def unlimited_tool() -> ToolResult:
            return ToolResult(content=[TextContent(type="text", text="y" * 10_000)])

        async with Client(mcp_server) as client:
            # Limited tool should be truncated
            result = await client.call_tool("limited_tool", {})
            assert "[Response truncated" in result.content[0].text

            # Unlimited tool should pass through
            result = await client.call_tool("unlimited_tool", {})
            assert "y" * 100 in result.content[0].text

    async def test_empty_tools_list_limits_nothing(self, mcp_server: FastMCP):
        """Test that empty tools list means no tools are limited."""
        mcp_server.add_middleware(ResponseLimitingMiddleware(max_size=100, tools=[]))

        @mcp_server.tool()
        def any_tool() -> ToolResult:
            return ToolResult(content=[TextContent(type="text", text="x" * 10_000)])

        async with Client(mcp_server) as client:
            result = await client.call_tool("any_tool", {})
            # Should NOT be truncated
            assert "[Response truncated" not in result.content[0].text

    async def test_custom_truncation_suffix(self, mcp_server: FastMCP):
        """Test that custom truncation suffix is applied."""
        mcp_server.add_middleware(
            ResponseLimitingMiddleware(max_size=200, truncation_suffix="\n[CUT]")
        )

        @mcp_server.tool()
        def large_tool() -> ToolResult:
            return ToolResult(content=[TextContent(type="text", text="x" * 10_000)])

        async with Client(mcp_server) as client:
            result = await client.call_tool("large_tool", {})
            assert "[CUT]" in result.content[0].text

    async def test_multiple_text_blocks_combined(self, mcp_server: FastMCP):
        """Test that multiple text blocks are combined when truncating."""
        mcp_server.add_middleware(ResponseLimitingMiddleware(max_size=300))

        @mcp_server.tool()
        def multi_block() -> ToolResult:
            return ToolResult(
                content=[
                    TextContent(type="text", text="First: " + "a" * 500),
                    TextContent(type="text", text="Second: " + "b" * 500),
                ]
            )

        async with Client(mcp_server) as client:
            result = await client.call_tool("multi_block", {})
            # Both blocks should be joined and truncated
            assert len(result.content) == 1
            assert "[Response truncated" in result.content[0].text

    async def test_binary_only_content_serialized(self, mcp_server: FastMCP):
        """Test that binary-only responses fall back to serialized content."""
        mcp_server.add_middleware(ResponseLimitingMiddleware(max_size=200))

        @mcp_server.tool()
        def binary_tool() -> ToolResult:
            return ToolResult(
                content=[
                    ImageContent(type="image", data="x" * 10_000, mimeType="image/png")
                ]
            )

        async with Client(mcp_server) as client:
            result = await client.call_tool("binary_tool", {})
            # Should be truncated (using serialized fallback)
            assert len(result.content) == 1
            assert "[Response truncated" in result.content[0].text

    async def test_default_max_size_is_1mb(self):
        """Test that the default max size is 1MB."""
        middleware = ResponseLimitingMiddleware()
        assert middleware.max_size == 1_000_000

    def test_invalid_max_size_raises(self):
        """Test that zero or negative max_size raises ValueError."""
        with pytest.raises(ValueError, match="max_size must be positive"):
            ResponseLimitingMiddleware(max_size=0)
        with pytest.raises(ValueError, match="max_size must be positive"):
            ResponseLimitingMiddleware(max_size=-100)

    def test_utf8_truncation_preserves_characters(self):
        """Test that UTF-8 truncation doesn't break multi-byte characters."""
        middleware = ResponseLimitingMiddleware(max_size=100)
        # Text with multi-byte characters (emoji)
        text = "Hello 🌍 World 🎉 Test " * 100
        result = middleware._truncate_to_result(text)
        # Should not raise and should be valid UTF-8
        content = result.content[0]
        assert isinstance(content, TextContent)
        content.text.encode("utf-8")
