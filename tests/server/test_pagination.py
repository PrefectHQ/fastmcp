"""Tests for MCP pagination support."""

from __future__ import annotations

import pytest
from mcp.shared.exceptions import McpError

from fastmcp import Client, FastMCP
from fastmcp.utilities.pagination import CursorState, paginate_sequence


class TestCursorEncoding:
    """Tests for cursor encoding/decoding."""

    def test_encode_decode_roundtrip(self) -> None:
        """Cursor should survive encode/decode roundtrip."""
        state = CursorState(offset=100)
        encoded = state.encode()
        decoded = CursorState.decode(encoded)
        assert decoded.offset == 100

    def test_encode_produces_string(self) -> None:
        """Encoded cursor should be a string."""
        state = CursorState(offset=50)
        encoded = state.encode()
        assert isinstance(encoded, str)
        assert len(encoded) > 0

    def test_decode_invalid_base64_raises(self) -> None:
        """Invalid base64 should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid cursor"):
            CursorState.decode("not-valid-base64!!!")

    def test_decode_invalid_json_raises(self) -> None:
        """Valid base64 but invalid JSON should raise ValueError."""
        import base64

        invalid = base64.urlsafe_b64encode(b"not json").decode()
        with pytest.raises(ValueError, match="Invalid cursor"):
            CursorState.decode(invalid)

    def test_decode_missing_offset_raises(self) -> None:
        """JSON missing the offset key should raise ValueError."""
        import base64
        import json

        invalid = base64.urlsafe_b64encode(json.dumps({"x": 1}).encode()).decode()
        with pytest.raises(ValueError, match="Invalid cursor"):
            CursorState.decode(invalid)


class TestPaginateSequence:
    """Tests for the paginate_sequence helper."""

    def test_first_page_no_cursor(self) -> None:
        """First page should start from beginning."""
        items = list(range(25))
        page, cursor = paginate_sequence(items, None, 10)
        assert page == list(range(10))
        assert cursor is not None

    def test_second_page_with_cursor(self) -> None:
        """Second page should continue from cursor."""
        items = list(range(25))
        _, cursor = paginate_sequence(items, None, 10)
        page, next_cursor = paginate_sequence(items, cursor, 10)
        assert page == list(range(10, 20))
        assert next_cursor is not None

    def test_last_page_returns_none_cursor(self) -> None:
        """Last page should return None cursor."""
        items = list(range(25))
        _, c1 = paginate_sequence(items, None, 10)
        _, c2 = paginate_sequence(items, c1, 10)
        page, next_cursor = paginate_sequence(items, c2, 10)
        assert page == list(range(20, 25))
        assert next_cursor is None

    def test_empty_list(self) -> None:
        """Empty list should return empty page and no cursor."""
        page, cursor = paginate_sequence([], None, 10)
        assert page == []
        assert cursor is None

    def test_exact_page_size(self) -> None:
        """List exactly matching page size should return no cursor."""
        items = list(range(10))
        page, cursor = paginate_sequence(items, None, 10)
        assert page == items
        assert cursor is None

    def test_smaller_than_page_size(self) -> None:
        """List smaller than page size should return all items."""
        items = list(range(5))
        page, cursor = paginate_sequence(items, None, 10)
        assert page == items
        assert cursor is None

    def test_invalid_cursor_raises(self) -> None:
        """Invalid cursor should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid cursor"):
            paginate_sequence([1, 2, 3], "invalid!", 10)


class TestServerPagination:
    """Integration tests for server pagination."""

    async def test_tools_pagination_returns_all_tools(self) -> None:
        """Client should receive all tools across paginated requests."""
        server = FastMCP(list_page_size=10)

        for i in range(25):

            @server.tool(name=f"tool_{i}")
            def make_tool() -> str:
                return "ok"

        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 25
            tool_names = {t.name for t in tools}
            assert tool_names == {f"tool_{i}" for i in range(25)}

    async def test_resources_pagination_returns_all_resources(self) -> None:
        """Client should receive all resources across paginated requests."""
        server = FastMCP(list_page_size=10)

        for i in range(25):

            @server.resource(f"test://resource_{i}")
            def make_resource() -> str:
                return "data"

        async with Client(server) as client:
            resources = await client.list_resources()
            assert len(resources) == 25

    async def test_prompts_pagination_returns_all_prompts(self) -> None:
        """Client should receive all prompts across paginated requests."""
        server = FastMCP(list_page_size=10)

        for i in range(25):

            @server.prompt(name=f"prompt_{i}")
            def make_prompt() -> str:
                return "text"

        async with Client(server) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 25

    async def test_manual_pagination(self) -> None:
        """Client can manually paginate using cursor."""
        server = FastMCP(list_page_size=10)

        for i in range(25):

            @server.tool(name=f"tool_{i}")
            def make_tool() -> str:
                return "ok"

        async with Client(server) as client:
            # First page
            result = await client.list_tools_mcp()
            assert len(result.tools) == 10
            assert result.nextCursor is not None

            # Second page
            result2 = await client.list_tools_mcp(cursor=result.nextCursor)
            assert len(result2.tools) == 10
            assert result2.nextCursor is not None

            # Third (last) page
            result3 = await client.list_tools_mcp(cursor=result2.nextCursor)
            assert len(result3.tools) == 5
            assert result3.nextCursor is None

    async def test_invalid_cursor_returns_error(self) -> None:
        """Server should return MCP error for invalid cursor."""
        server = FastMCP(list_page_size=10)

        @server.tool
        def my_tool() -> str:
            return "ok"

        async with Client(server) as client:
            with pytest.raises(McpError) as exc:
                await client.list_tools_mcp(cursor="invalid!")
            assert exc.value.error.code == -32602

    async def test_no_pagination_when_disabled(self) -> None:
        """Without list_page_size, all items returned at once."""
        server = FastMCP()  # No pagination

        for i in range(25):

            @server.tool(name=f"tool_{i}")
            def make_tool() -> str:
                return "ok"

        async with Client(server) as client:
            result = await client.list_tools_mcp()
            assert len(result.tools) == 25
            assert result.nextCursor is None

    async def test_pagination_exact_page_boundary(self) -> None:
        """Test pagination at exact page boundaries."""
        server = FastMCP(list_page_size=10)

        for i in range(20):  # Exactly 2 pages

            @server.tool(name=f"tool_{i}")
            def make_tool() -> str:
                return "ok"

        async with Client(server) as client:
            # First page
            result = await client.list_tools_mcp()
            assert len(result.tools) == 10
            assert result.nextCursor is not None

            # Second (last) page
            result2 = await client.list_tools_mcp(cursor=result.nextCursor)
            assert len(result2.tools) == 10
            assert result2.nextCursor is None


class TestPageSizeValidation:
    """Tests for list_page_size validation."""

    def test_zero_page_size_raises(self) -> None:
        """Zero page size should raise ValueError."""
        with pytest.raises(
            ValueError, match="list_page_size must be a positive integer"
        ):
            FastMCP(list_page_size=0)

    def test_negative_page_size_raises(self) -> None:
        """Negative page size should raise ValueError."""
        with pytest.raises(
            ValueError, match="list_page_size must be a positive integer"
        ):
            FastMCP(list_page_size=-1)
