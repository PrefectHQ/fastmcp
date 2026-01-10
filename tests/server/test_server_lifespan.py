"""Tests for server_lifespan and session_lifespan behavior."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.lifespan import ContextManagerLifespan, lifespan
from fastmcp.utilities.lifespan import combine_lifespans


class TestServerLifespan:
    """Test server_lifespan functionality."""

    async def test_server_lifespan_basic(self):
        """Test that server_lifespan is entered once and persists across sessions."""
        lifespan_events: list[str] = []

        @asynccontextmanager
        async def server_lifespan(mcp: FastMCP) -> AsyncIterator[dict[str, Any]]:
            lifespan_events.append("enter")
            try:
                yield {"initialized": True}
            finally:
                lifespan_events.append("exit")

        mcp = FastMCP("TestServer", lifespan=server_lifespan)

        @mcp.tool
        def get_value() -> str:
            return "test"

        # Server lifespan should be entered when run_async starts
        assert lifespan_events == []

        # Connect first client session
        async with Client(mcp) as client1:
            result1 = await client1.call_tool("get_value", {})
            assert result1.data == "test"
            # Server lifespan should have been entered once
            assert lifespan_events == ["enter"]

            # Connect second client session while first is still active
            async with Client(mcp) as client2:
                result2 = await client2.call_tool("get_value", {})
                assert result2.data == "test"
                # Server lifespan should still only have been entered once
                assert lifespan_events == ["enter"]

        # Because we're using a fastmcptransport, the server lifespan should be exited
        # when the client session closes
        assert lifespan_events == ["enter", "exit"]

    async def test_server_lifespan_context_available(self):
        """Test that server_lifespan context is available to tools."""

        @asynccontextmanager
        async def server_lifespan(mcp: FastMCP) -> AsyncIterator[dict]:
            yield {"db_connection": "mock_db"}

        mcp = FastMCP("TestServer", lifespan=server_lifespan)

        @mcp.tool
        def get_db_info(ctx: Context) -> str:
            # Access the server lifespan context
            assert ctx.request_context is not None  # type narrowing for type checker
            lifespan_context = ctx.request_context.lifespan_context
            return lifespan_context.get("db_connection", "no_db")

        async with Client(mcp) as client:
            result = await client.call_tool("get_db_info", {})
            assert result.data == "mock_db"


class TestComposableLifespans:
    """Test composable lifespan functionality."""

    async def test_lifespan_decorator_basic(self):
        """Test that the @lifespan decorator works like @asynccontextmanager."""
        events: list[str] = []

        @lifespan
        async def my_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            events.append("enter")
            try:
                yield {"key": "value"}
            finally:
                events.append("exit")

        mcp = FastMCP("TestServer", lifespan=my_lifespan)

        @mcp.tool
        def get_info(ctx: Context) -> str:
            assert ctx.request_context is not None
            lifespan_context = ctx.request_context.lifespan_context
            return lifespan_context.get("key", "missing")

        assert events == []

        async with Client(mcp) as client:
            result = await client.call_tool("get_info", {})
            assert result.data == "value"
            assert events == ["enter"]

        assert events == ["enter", "exit"]

    async def test_lifespan_composition_two(self):
        """Test composing two lifespans with |."""
        events: list[str] = []

        @lifespan
        async def first_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            events.append("first_enter")
            try:
                yield {"first": "a"}
            finally:
                events.append("first_exit")

        @lifespan
        async def second_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            events.append("second_enter")
            try:
                yield {"second": "b"}
            finally:
                events.append("second_exit")

        composed = first_lifespan | second_lifespan
        mcp = FastMCP("TestServer", lifespan=composed)

        @mcp.tool
        def get_both(ctx: Context) -> dict:
            assert ctx.request_context is not None
            return dict(ctx.request_context.lifespan_context)

        async with Client(mcp) as client:
            result = await client.call_tool("get_both", {})
            # Results should be merged
            assert result.data == {"first": "a", "second": "b"}
            # Should enter in order
            assert events == ["first_enter", "second_enter"]

        # Should exit in reverse order (LIFO)
        assert events == ["first_enter", "second_enter", "second_exit", "first_exit"]

    async def test_lifespan_composition_three(self):
        """Test composing three lifespans with |."""
        events: list[str] = []

        @lifespan
        async def ls_a(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            events.append("a_enter")
            try:
                yield {"a": 1}
            finally:
                events.append("a_exit")

        @lifespan
        async def ls_b(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            events.append("b_enter")
            try:
                yield {"b": 2}
            finally:
                events.append("b_exit")

        @lifespan
        async def ls_c(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            events.append("c_enter")
            try:
                yield {"c": 3}
            finally:
                events.append("c_exit")

        composed = ls_a | ls_b | ls_c
        mcp = FastMCP("TestServer", lifespan=composed)

        @mcp.tool
        def get_all(ctx: Context) -> dict:
            assert ctx.request_context is not None
            return dict(ctx.request_context.lifespan_context)

        async with Client(mcp) as client:
            result = await client.call_tool("get_all", {})
            assert result.data == {"a": 1, "b": 2, "c": 3}
            assert events == ["a_enter", "b_enter", "c_enter"]

        assert events == [
            "a_enter",
            "b_enter",
            "c_enter",
            "c_exit",
            "b_exit",
            "a_exit",
        ]

    async def test_lifespan_result_merge_later_wins(self):
        """Test that later lifespans overwrite earlier ones on key conflict."""

        @lifespan
        async def first(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            yield {"key": "first", "only_first": "yes"}

        @lifespan
        async def second(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            yield {"key": "second", "only_second": "yes"}

        composed = first | second
        mcp = FastMCP("TestServer", lifespan=composed)

        @mcp.tool
        def get_context(ctx: Context) -> dict:
            assert ctx.request_context is not None
            return dict(ctx.request_context.lifespan_context)

        async with Client(mcp) as client:
            result = await client.call_tool("get_context", {})
            # "key" should be overwritten by second
            assert result.data == {
                "key": "second",
                "only_first": "yes",
                "only_second": "yes",
            }

    async def test_lifespan_composition_with_context_manager_lifespan(self):
        """Test composing with ContextManagerLifespan for @asynccontextmanager functions."""
        events: list[str] = []

        @asynccontextmanager
        async def legacy_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            events.append("legacy_enter")
            try:
                yield {"legacy": True}
            finally:
                events.append("legacy_exit")

        @lifespan
        async def new_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            events.append("new_enter")
            try:
                yield {"new": True}
            finally:
                events.append("new_exit")

        # Wrap the @asynccontextmanager function explicitly
        composed = ContextManagerLifespan(legacy_lifespan) | new_lifespan
        mcp = FastMCP("TestServer", lifespan=composed)

        @mcp.tool
        def get_context(ctx: Context) -> dict:
            assert ctx.request_context is not None
            return dict(ctx.request_context.lifespan_context)

        async with Client(mcp) as client:
            result = await client.call_tool("get_context", {})
            assert result.data == {"legacy": True, "new": True}

        assert events == [
            "legacy_enter",
            "new_enter",
            "new_exit",
            "legacy_exit",
        ]

    async def test_backwards_compatibility_asynccontextmanager(self):
        """Test that existing @asynccontextmanager lifespans still work."""

        @asynccontextmanager
        async def old_style_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            yield {"old_style": True}

        mcp = FastMCP("TestServer", lifespan=old_style_lifespan)

        @mcp.tool
        def get_context(ctx: Context) -> dict:
            assert ctx.request_context is not None
            return dict(ctx.request_context.lifespan_context)

        async with Client(mcp) as client:
            result = await client.call_tool("get_context", {})
            assert result.data == {"old_style": True}

    async def test_lifespan_or_requires_lifespan_instance(self):
        """Test that | operator requires Lifespan instances and gives helpful error."""

        @lifespan
        async def my_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            yield {"key": "value"}

        @asynccontextmanager
        async def regular_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
            yield {"regular": True}

        # Composing with non-Lifespan should raise TypeError with helpful message
        with pytest.raises(TypeError) as exc_info:
            my_lifespan | regular_lifespan  # type: ignore[operator]

        assert "ContextManagerLifespan" in str(exc_info.value)


class TestCombineLifespans:
    """Test combine_lifespans utility function."""

    async def test_combine_lifespans_fastapi_style(self):
        """Test combining lifespans that yield None (FastAPI-style)."""
        events: list[str] = []

        @asynccontextmanager
        async def first_lifespan(app: Any) -> AsyncIterator[None]:
            events.append("first_enter")
            try:
                yield
            finally:
                events.append("first_exit")

        @asynccontextmanager
        async def second_lifespan(app: Any) -> AsyncIterator[None]:
            events.append("second_enter")
            try:
                yield
            finally:
                events.append("second_exit")

        combined = combine_lifespans(first_lifespan, second_lifespan)

        async with combined("mock_app") as result:
            assert result == {}  # Empty dict when lifespans yield None
            assert events == ["first_enter", "second_enter"]

        # LIFO exit order
        assert events == ["first_enter", "second_enter", "second_exit", "first_exit"]

    async def test_combine_lifespans_fastmcp_style(self):
        """Test combining lifespans that yield dicts (FastMCP-style)."""
        events: list[str] = []

        @asynccontextmanager
        async def db_lifespan(app: Any) -> AsyncIterator[dict[str, Any]]:
            events.append("db_enter")
            try:
                yield {"db": "connected"}
            finally:
                events.append("db_exit")

        @asynccontextmanager
        async def cache_lifespan(app: Any) -> AsyncIterator[dict[str, Any]]:
            events.append("cache_enter")
            try:
                yield {"cache": "ready"}
            finally:
                events.append("cache_exit")

        combined = combine_lifespans(db_lifespan, cache_lifespan)

        async with combined("mock_app") as result:
            assert result == {"db": "connected", "cache": "ready"}
            assert events == ["db_enter", "cache_enter"]

        assert events == ["db_enter", "cache_enter", "cache_exit", "db_exit"]

    async def test_combine_lifespans_mixed_styles(self):
        """Test combining FastAPI-style (yield None) and FastMCP-style (yield dict)."""
        events: list[str] = []

        @asynccontextmanager
        async def fastapi_lifespan(app: Any) -> AsyncIterator[None]:
            events.append("fastapi_enter")
            try:
                yield  # FastAPI-style: yield None
            finally:
                events.append("fastapi_exit")

        @asynccontextmanager
        async def fastmcp_lifespan(app: Any) -> AsyncIterator[dict[str, Any]]:
            events.append("fastmcp_enter")
            try:
                yield {"mcp": "initialized"}  # FastMCP-style: yield dict
            finally:
                events.append("fastmcp_exit")

        combined = combine_lifespans(fastapi_lifespan, fastmcp_lifespan)

        async with combined("mock_app") as result:
            # Only the dict from fastmcp_lifespan should be present
            assert result == {"mcp": "initialized"}
            assert events == ["fastapi_enter", "fastmcp_enter"]

        assert events == [
            "fastapi_enter",
            "fastmcp_enter",
            "fastmcp_exit",
            "fastapi_exit",
        ]

    async def test_combine_lifespans_result_merge_later_wins(self):
        """Test that later lifespans overwrite earlier ones on key conflict."""

        @asynccontextmanager
        async def first(app: Any) -> AsyncIterator[dict[str, Any]]:
            yield {"key": "first", "only_first": "yes"}

        @asynccontextmanager
        async def second(app: Any) -> AsyncIterator[dict[str, Any]]:
            yield {"key": "second", "only_second": "yes"}

        combined = combine_lifespans(first, second)

        async with combined("mock_app") as result:
            assert result == {
                "key": "second",  # Overwritten by later lifespan
                "only_first": "yes",
                "only_second": "yes",
            }

    async def test_combine_lifespans_three(self):
        """Test combining three lifespans."""
        events: list[str] = []

        @asynccontextmanager
        async def ls_a(app: Any) -> AsyncIterator[dict[str, Any]]:
            events.append("a_enter")
            try:
                yield {"a": 1}
            finally:
                events.append("a_exit")

        @asynccontextmanager
        async def ls_b(app: Any) -> AsyncIterator[dict[str, Any]]:
            events.append("b_enter")
            try:
                yield {"b": 2}
            finally:
                events.append("b_exit")

        @asynccontextmanager
        async def ls_c(app: Any) -> AsyncIterator[dict[str, Any]]:
            events.append("c_enter")
            try:
                yield {"c": 3}
            finally:
                events.append("c_exit")

        combined = combine_lifespans(ls_a, ls_b, ls_c)

        async with combined("mock_app") as result:
            assert result == {"a": 1, "b": 2, "c": 3}
            assert events == ["a_enter", "b_enter", "c_enter"]

        assert events == [
            "a_enter",
            "b_enter",
            "c_enter",
            "c_exit",
            "b_exit",
            "a_exit",
        ]

    async def test_combine_lifespans_empty(self):
        """Test combining zero lifespans."""
        combined = combine_lifespans()

        async with combined("mock_app") as result:
            assert result == {}
