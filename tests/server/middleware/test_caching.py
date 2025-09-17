"""Tests for response caching middleware."""

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import mcp.types
import pytest
from inline_snapshot import snapshot
from pydantic import BaseModel

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.middleware.caching import (
    CacheEntry,
    CacheStats,
    InMemoryCache,
    ResponseCachingMiddleware,
)
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult


class CrazyModel(BaseModel):
    a: int
    b: int
    c: str
    d: float
    e: bool
    f: list[int]
    g: dict[str, int]
    h: list[dict[str, int]]
    i: dict[str, list[int]]


def extract_content_for_snapshot(result: ToolResult) -> dict[str, Any]:
    return {
        "content": [c.model_dump() for c in result.content],
        "structured_content": result.structured_content,
    }


@pytest.fixture
def crazy_model():
    return CrazyModel(
        a=5,
        b=10,
        c="test",
        d=1.0,
        e=True,
        f=[1, 2, 3],
        g={"a": 1, "b": 2},
        h=[{"a": 1, "b": 2}],
        i={"a": [1, 2]},
    )


class TrackingCalculator:
    add_calls: int
    multiply_calls: int
    crazy_calls: int

    def __init__(self):
        self.add_calls = 0
        self.multiply_calls = 0
        self.crazy_calls = 0

    def add(self, a: int, b: int) -> int:
        self.add_calls += 1
        return a + b

    def multiply(self, a: int, b: int) -> int:
        self.multiply_calls += 1
        return a * b

    def crazy(self, a: CrazyModel) -> CrazyModel:
        self.crazy_calls += 1
        return a

    def add_tools(self, fastmcp: FastMCP):
        fastmcp.add_tool(tool=Tool.from_function(fn=self.add))
        fastmcp.add_tool(tool=Tool.from_function(fn=self.multiply))
        fastmcp.add_tool(tool=Tool.from_function(fn=self.crazy))


@pytest.fixture
def tracking_calculator():
    return TrackingCalculator()


@pytest.fixture
def mock_context():
    """Create a mock middleware context for tool calls."""
    context = MagicMock(spec=MiddlewareContext[mcp.types.CallToolRequestParams])
    context.message = mcp.types.CallToolRequestParams(
        name="test_tool", arguments={"param1": "value1", "param2": 42}
    )
    context.method = "tools/call"
    return context


@pytest.fixture
def mock_call_next():
    """Create a mock call_next function."""
    return AsyncMock(
        return_value=ToolResult(
            content=[{"type": "text", "text": "test result"}],
            structured_content={"result": "success", "value": 123},
        )
    )


@pytest.fixture
def sample_tool_result():
    """Create a sample tool result for testing."""
    return ToolResult(
        content=[{"type": "text", "text": "cached result"}],
        structured_content={"cached": True, "data": "test"},
    )


class TestCacheEntry:
    """Test CacheEntry class functionality."""

    def test_init_and_expiration(self):
        """Test cache entry initialization and expiration logic."""
        now = datetime.now(tz=timezone.utc)
        future = now + timedelta(seconds=3600)
        past = now - timedelta(seconds=3600)

        # Test valid entry
        entry = CacheEntry(
            key="test_key",
            content=[{"type": "text", "text": "test"}],
            structured_content='{"result": "success"}',
            created_at=now,
            expires_at=future,
        )

        assert entry.key == "test_key"
        assert not entry.is_expired()

        # Test expired entry
        expired_entry = CacheEntry(
            key="expired_key",
            content=None,
            structured_content=None,
            created_at=past,
            expires_at=past,
        )

        assert expired_entry.is_expired()

    def test_serialization(self):
        """Test cache entry serialization to/from tool result."""
        tool_result = ToolResult(
            content=[{"type": "text", "text": "test"}],
            structured_content={"result": "success"},
        )

        # Test round-trip conversion
        entry = CacheEntry.from_tool_result("test_key", tool_result, 3600)
        result = entry.to_tool_result()

        assert result.content == tool_result.content
        assert result.structured_content == tool_result.structured_content


class TestInMemoryCache:
    """Test InMemoryCache implementation."""

    async def test_basic_operations(self, sample_tool_result):
        """Test basic cache operations."""
        cache = InMemoryCache()

        # Test set and get
        await cache.set("test_key", sample_tool_result, 3600)
        result = await cache.get("test_key")

        assert result is not None
        assert result.content == sample_tool_result.content

        # Test delete
        await cache.delete("test_key")
        assert await cache.get("test_key") is None

    async def test_expiration_and_cleanup(self, sample_tool_result):
        """Test cache expiration and cleanup."""
        cache = InMemoryCache()

        # Create an expired entry
        entry = CacheEntry.from_tool_result("expired_key", sample_tool_result, -1)
        cache._cache["expired_key"] = entry

        # Should return None and remove expired entry
        result = await cache.get("expired_key")
        assert result is None
        assert "expired_key" not in cache._cache

    async def test_size_limit(self, sample_tool_result):
        """Test cache size limit enforcement."""
        cache = InMemoryCache(max_size=2)

        # Fill cache to capacity
        await cache.set("key1", sample_tool_result, 3600)
        await cache.set("key2", sample_tool_result, 3600)

        # Add one more - should evict the first
        await cache.set("key3", sample_tool_result, 3600)

        assert len(cache._cache) == 2
        assert "key1" not in cache._cache
        assert "key2" in cache._cache
        assert "key3" in cache._cache


class TestResponseCachingMiddleware:
    """Test ResponseCachingMiddleware functionality."""

    def test_initialization(self):
        """Test middleware initialization."""
        cache = InMemoryCache()
        middleware = ResponseCachingMiddleware(
            cache_backend=cache,
            included_tools=["tool1"],
            excluded_tools=["tool2"],
            default_ttl=1800,
        )

        assert middleware._backend is cache
        assert middleware._default_ttl == 1800
        assert middleware._included_tools == ["tool1"]
        assert middleware._excluded_tools == ["tool2"]
        assert middleware._stats.hits == 0
        assert middleware._stats.misses == 0

    def test_tool_filtering(self):
        """Test tool filtering logic."""
        cache = InMemoryCache()

        # Test included tools only
        middleware1 = ResponseCachingMiddleware(
            cache, included_tools=["tool1", "tool2"]
        )
        assert middleware1._should_cache_tool("tool1") is True
        assert middleware1._should_cache_tool("tool3") is False

        # Test excluded tools
        middleware2 = ResponseCachingMiddleware(cache, excluded_tools=["tool1"])
        assert middleware2._should_cache_tool("tool1") is False
        assert middleware2._should_cache_tool("tool2") is True

        # Test both (excluded takes precedence)
        middleware3 = ResponseCachingMiddleware(
            cache, included_tools=["tool1", "tool2"], excluded_tools=["tool2"]
        )
        assert middleware3._should_cache_tool("tool1") is True
        assert middleware3._should_cache_tool("tool2") is False

    def test_cache_key_generation(self):
        """Test cache key generation."""
        cache = InMemoryCache()
        middleware = ResponseCachingMiddleware(cache)

        msg = mcp.types.CallToolRequestParams(
            name="test_tool", arguments={"param1": "value1", "param2": 42}
        )

        key = middleware._make_cache_key(msg)

        # Should be a SHA256 hash
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    async def test_cache_miss_and_hit(self, mock_context, mock_call_next):
        """Test cache miss and hit scenarios."""
        cache = InMemoryCache()
        middleware = ResponseCachingMiddleware(cache)

        # First call - cache miss
        result1 = await middleware.on_call_tool(mock_context, mock_call_next)
        assert middleware._stats.misses == 1
        assert middleware._stats.hits == 0

        # Second call - cache hit
        mock_call_next.reset_mock()
        result2 = await middleware.on_call_tool(mock_context, mock_call_next)

        assert result1.content == result2.content
        assert not mock_call_next.called  # Should not call downstream
        assert middleware._stats.hits == 1
        assert middleware._stats.misses == 1


class TestResponseCachingMiddlewareIntegration:
    """Integration tests with real FastMCP server."""

    @pytest.fixture
    def caching_server(self, tracking_calculator: TrackingCalculator):
        """Create a FastMCP server for caching tests."""
        mcp = FastMCP("CachingTestServer")

        mcp.add_middleware(
            middleware=ResponseCachingMiddleware(cache_backend=InMemoryCache())
        )

        tracking_calculator.add_tools(mcp)

        return mcp

    @pytest.fixture
    def non_caching_server(self, tracking_calculator: TrackingCalculator):
        """Create a FastMCP server for non-caching tests."""
        mcp = FastMCP("NonCachingTestServer")
        tracking_calculator.add_tools(mcp)
        return mcp

    async def test_caching_works_with_real_server(
        self,
        caching_server: FastMCP,
        tracking_calculator: TrackingCalculator,
        crazy_model: CrazyModel,
    ):
        """Test that caching works with a real FastMCP server."""
        tracking_calculator.add_tools(caching_server)

        async with Client(caching_server) as client:
            call_tool_result = await client.call_tool("add", {"a": 5, "b": 3})

            assert tracking_calculator.add_calls == 1
            assert extract_content_for_snapshot(call_tool_result) == snapshot(
                {
                    "content": [
                        {"type": "text", "text": "8", "annotations": None, "meta": None}
                    ],
                    "structured_content": {"result": 8},
                }
            )

            call_tool_result = await client.call_tool("add", {"a": 5, "b": 3})
            assert tracking_calculator.add_calls == 1
            assert extract_content_for_snapshot(call_tool_result) == snapshot(
                {
                    "content": [
                        {"type": "text", "text": "8", "annotations": None, "meta": None}
                    ],
                    "structured_content": {"result": 8},
                }
            )

            call_tool_result = await client.call_tool("crazy", {"a": crazy_model})
            assert tracking_calculator.crazy_calls == 1
            assert extract_content_for_snapshot(call_tool_result) == snapshot(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"a":5,"b":10,"c":"test","d":1.0,"e":true,"f":[1,2,3],"g":{"a":1,"b":2},"h":[{"a":1,"b":2}],"i":{"a":[1,2]}}',
                            "annotations": None,
                            "meta": None,
                        }
                    ],
                    "structured_content": {
                        "a": 5,
                        "b": 10,
                        "c": "test",
                        "d": 1.0,
                        "e": True,
                        "f": [1, 2, 3],
                        "g": {"a": 1, "b": 2},
                        "h": [{"a": 1, "b": 2}],
                        "i": {"a": [1, 2]},
                    },
                }
            )

            call_tool_result = await client.call_tool("crazy", {"a": crazy_model})
            assert tracking_calculator.crazy_calls == 1
            assert extract_content_for_snapshot(call_tool_result) == snapshot(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"a":5,"b":10,"c":"test","d":1.0,"e":true,"f":[1,2,3],"g":{"a":1,"b":2},"h":[{"a":1,"b":2}],"i":{"a":[1,2]}}',
                            "annotations": None,
                            "meta": None,
                        }
                    ],
                    "structured_content": {
                        "a": 5,
                        "b": 10,
                        "c": "test",
                        "d": 1.0,
                        "e": True,
                        "f": [1, 2, 3],
                        "g": {"a": 1, "b": 2},
                        "h": [{"a": 1, "b": 2}],
                        "i": {"a": [1, 2]},
                    },
                }
            )

    async def test_different_arguments_create_different_entries(
        self, caching_server: FastMCP, tracking_calculator: TrackingCalculator
    ):
        """Test that different arguments create different cache entries."""

        async with Client(caching_server) as client:
            result1 = await client.call_tool("add", {"a": 5, "b": 10})
            assert tracking_calculator.add_calls == 1
            result2 = await client.call_tool("add", {"a": 1, "b": 5})
            assert tracking_calculator.add_calls == 2

            # Results should be different
            assert result1.structured_content["result"] == 15
            assert result2.structured_content["result"] == 6

    async def test_tool_filtering_integration(
        self, non_caching_server: FastMCP, tracking_calculator: TrackingCalculator
    ):
        """Test tool filtering in integration."""
        partial_caching_server = non_caching_server

        partial_caching_server.add_middleware(
            ResponseCachingMiddleware(
                cache_backend=InMemoryCache(),
                included_tools=["add"],  # Only cache this tool
            )
        )

        async with Client(partial_caching_server) as client:
            # This should be cached
            await client.call_tool("add", {"a": 5, "b": 10})
            await client.call_tool("add", {"a": 5, "b": 10})
            assert tracking_calculator.add_calls == 1

            # This should not be cached
            await client.call_tool("multiply", {"a": 1, "b": 5})
            await client.call_tool("multiply", {"a": 1, "b": 5})
            assert tracking_calculator.multiply_calls == 2

    async def test_cache_stats_tracking(self, non_caching_server: FastMCP):
        """Test that cache statistics are properly tracked."""
        middleware = ResponseCachingMiddleware(cache_backend=InMemoryCache())
        non_caching_server.add_middleware(middleware)

        async with Client(non_caching_server) as client:
            # First call - cache miss
            await client.call_tool("add", {"a": 5, "b": 10})
            assert middleware._stats.misses == 1
            assert middleware._stats.hits == 0

            # Second call - cache hit
            await client.call_tool("add", {"a": 5, "b": 10})
            assert middleware._stats.misses == 1
            assert middleware._stats.hits == 1


class TestCacheStats:
    """Test CacheStats functionality."""

    def test_stats_initialization(self):
        """Test cache stats initialization."""
        stats = CacheStats(hits=5, misses=10)
        assert stats.hits == 5
        assert stats.misses == 10
