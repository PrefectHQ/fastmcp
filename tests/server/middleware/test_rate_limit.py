"""Comprehensive tests for rate limiting middleware."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.server.middleware.rate_limiting import (
    RateLimitError,
    RateLimitExceededError,
    RateLimitingMiddleware,
    TokenBucketRateLimiter,
    rate_limit,
)
from fastmcp.server.middleware._rate_limit_backend import (
    InMemoryBackend,
    RateLimitBackend,
)


@pytest.fixture
def mock_context():
    """Create a mock middleware context."""
    context = MagicMock(spec=MiddlewareContext)
    context.method = "tools/call"
    context.message = MagicMock()
    context.message.name = "test_tool"
    return context


@pytest.fixture
def mock_call_next():
    """Create a mock call_next function."""
    return AsyncMock(return_value="test_result")


class TestRateLimitExceededError:
    """Test RateLimitExceededError class."""

    def test_init_default(self):
        """Test default initialization."""
        error = RateLimitExceededError()
        assert error.error.code == -32000
        assert "Rate limit exceeded" in error.error.message
        assert "Retry after" in error.error.message
        assert error.retry_after == 1.0

    def test_init_custom(self):
        """Test custom initialization."""
        error = RateLimitExceededError(
            message="Custom rate limit message", retry_after=2.5
        )
        assert error.error.code == -32000
        assert "Custom rate limit message" in error.error.message
        assert "Retry after 2.50 seconds" in error.error.message
        assert error.retry_after == 2.5

    def test_alias(self):
        """Test that RateLimitError is an alias for RateLimitExceededError."""
        assert RateLimitError is RateLimitExceededError


class TestTokenBucketRateLimiter:
    """Test token bucket rate limiter with time control."""

    def test_init(self):
        """Test initialization."""
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=5.0)
        assert limiter.capacity == 10
        assert limiter.refill_rate == 5.0
        assert limiter.tokens == 10

    async def test_consume_success(self):
        """Test successful token consumption."""
        base_time = time.time()
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=5.0)
        limiter.last_refill = base_time

        assert await limiter.consume(5) is True
        assert limiter.tokens == pytest.approx(5.0, abs=0.01)
        assert await limiter.consume(3) is True
        assert limiter.tokens == pytest.approx(2.0, abs=0.01)

    async def test_consume_failure(self):
        """Test failed token consumption."""
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)

        assert await limiter.consume(5) is True
        assert limiter.tokens == 0
        assert await limiter.consume(1) is False

    async def test_refill_over_time(self, monkeypatch):
        """Test token refill over time using monkeypatch."""
        base_time = time.time()
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=10.0)
        limiter.last_refill = base_time

        assert await limiter.consume(10) is True
        assert limiter.tokens == pytest.approx(0.0, abs=0.01)
        assert await limiter.consume(1) is False

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        limiter._refill()
        assert limiter.tokens == pytest.approx(5.0, abs=0.01)

        monkeypatch.setattr(time, "time", lambda: base_time + 1.0)

        limiter._refill()
        assert limiter.tokens == pytest.approx(10.0, abs=0.01)

    async def test_refill_cap_limits(self, monkeypatch):
        """Test that refill doesn't exceed capacity."""
        base_time = time.time()
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=10.0)

        assert await limiter.consume(5) is True
        assert limiter.tokens == 5

        monkeypatch.setattr(time, "time", lambda: base_time + 2.0)

        limiter._refill()
        assert limiter.tokens == 10.0

    def test_get_wait_time_available(self, monkeypatch):
        """Test get_wait_time when tokens are available."""
        base_time = time.time()
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=5.0)

        assert limiter.get_wait_time(1) == 0.0

        monkeypatch.setattr(time, "time", lambda: base_time + 1.0)
        assert limiter.get_wait_time(5) == 0.0

    def test_get_wait_time_needed(self, monkeypatch):
        """Test get_wait_time when tokens need to be refilled."""
        base_time = time.time()
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=5.0)

        limiter.tokens = 2
        limiter.last_refill = base_time

        wait_time = limiter.get_wait_time(5)
        assert wait_time == pytest.approx(0.6, abs=0.01)

        monkeypatch.setattr(time, "time", lambda: base_time + 0.3)
        wait_time = limiter.get_wait_time(5)
        assert wait_time == pytest.approx(0.3, abs=0.01)

    def test_get_wait_time_zero_refill_rate(self):
        """Test get_wait_time with zero refill rate."""
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=0.0)
        limiter.tokens = 0

        wait_time = limiter.get_wait_time(1)
        assert wait_time == float("inf")


class TestRateLimitingMiddleware:
    """Test RateLimitingMiddleware class."""

    def test_init_default(self):
        """Test default initialization."""
        middleware = RateLimitingMiddleware()
        assert middleware.global_limit is None
        assert middleware.per_tool_limit == {}
        assert middleware.per_client_limit is None
        assert middleware.get_client_id is None
        assert middleware._global_limiter is None

    def test_init_custom(self):
        """Test custom initialization."""

        def get_client_id(ctx):
            return "test_client"

        middleware = RateLimitingMiddleware(
            global_limit=10.0,
            burst_capacity=20,
            per_tool_limit={"search": 5.0, "write": 2.0},
            per_client_limit=100.0,
            get_client_id=get_client_id,
        )

        assert middleware.global_limit == 10.0
        assert middleware.burst_capacity == 20
        assert middleware.per_tool_limit == {"search": 5.0, "write": 2.0}
        assert middleware.per_client_limit == 100.0
        assert middleware.get_client_id is get_client_id
        assert middleware._global_limiter is not None
        assert middleware._global_limiter.capacity == 20
        assert middleware._global_limiter.refill_rate == 10.0
        assert "search" in middleware._tool_limiters
        assert "write" in middleware._tool_limiters

    def test_get_client_identifier_default(self, mock_context):
        """Test default client identifier."""
        middleware = RateLimitingMiddleware()
        assert middleware._get_client_identifier(mock_context) == "default"

    def test_get_client_identifier_custom(self, mock_context):
        """Test custom client identifier."""

        def get_client_id(ctx):
            return "custom_client"

        middleware = RateLimitingMiddleware(get_client_id=get_client_id)
        assert middleware._get_client_identifier(mock_context) == "custom_client"

    def test_get_tool_name(self, mock_context):
        """Test extracting tool name from context."""
        middleware = RateLimitingMiddleware()

        assert middleware._get_tool_name(mock_context) == "test_tool"

        mock_context.method = "resources/read"
        assert middleware._get_tool_name(mock_context) is None

    async def test_global_rate_limit_enforced(self, mock_context, mock_call_next, monkeypatch):
        """Test that global rate limit is enforced."""
        base_time = time.time()
        middleware = RateLimitingMiddleware(global_limit=1.0, burst_capacity=1)

        assert middleware._global_limiter is not None
        middleware._global_limiter.last_refill = base_time

        await middleware.on_request(mock_context, mock_call_next)
        assert mock_call_next.called

        mock_call_next.reset_mock()
        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await middleware.on_request(mock_context, mock_call_next)

        assert "Global rate limit exceeded" in str(exc_info.value.error.message)
        assert exc_info.value.retry_after > 0
        assert mock_call_next.called is False

    async def test_global_rate_limit_recovers(self, mock_context, mock_call_next, monkeypatch):
        """Test that global rate limit recovers after waiting."""
        base_time = time.time()
        middleware = RateLimitingMiddleware(global_limit=1.0, burst_capacity=1)

        assert middleware._global_limiter is not None
        middleware._global_limiter.last_refill = base_time

        await middleware.on_request(mock_context, mock_call_next)
        mock_call_next.reset_mock()

        monkeypatch.setattr(time, "time", lambda: base_time + 1.1)

        await middleware.on_request(mock_context, mock_call_next)
        assert mock_call_next.called

    async def test_per_tool_rate_limit_enforced(self, mock_context, mock_call_next, monkeypatch):
        """Test that per-tool rate limit is enforced."""
        base_time = time.time()
        middleware = RateLimitingMiddleware(
            per_tool_limit={"test_tool": 1.0},
            burst_capacity=1,
        )

        assert "test_tool" in middleware._tool_limiters
        middleware._tool_limiters["test_tool"].last_refill = base_time

        await middleware.on_request(mock_context, mock_call_next)
        assert mock_call_next.called

        mock_call_next.reset_mock()
        mock_context.message.name = "other_tool"
        await middleware.on_request(mock_context, mock_call_next)
        assert mock_call_next.called

        mock_call_next.reset_mock()
        mock_context.message.name = "test_tool"
        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await middleware.on_request(mock_context, mock_call_next)

        assert "Rate limit exceeded for tool: test_tool" in str(exc_info.value.error.message)
        assert exc_info.value.retry_after > 0

    async def test_per_client_rate_limit_enforced(self, mock_context, mock_call_next, monkeypatch):
        """Test that per-client rate limit is enforced."""
        base_time = time.time()
        client_calls = {"client_1": 0, "client_2": 0}

        def get_client_id(ctx):
            client_id = ctx.message.name
            client_calls[client_id] += 1
            return client_id

        middleware = RateLimitingMiddleware(
            per_client_limit=1.0,
            burst_capacity=1,
            get_client_id=get_client_id,
        )

        mock_context.message.name = "client_1"
        await middleware.on_request(mock_context, mock_call_next)
        assert mock_call_next.called
        assert "client_1" in middleware._client_limiters

        mock_call_next.reset_mock()
        mock_context.message.name = "client_2"
        await middleware.on_request(mock_context, mock_call_next)
        assert mock_call_next.called
        assert "client_2" in middleware._client_limiters

        mock_call_next.reset_mock()
        mock_context.message.name = "client_1"
        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await middleware.on_request(mock_context, mock_call_next)

        assert "Rate limit exceeded for client: client_1" in str(exc_info.value.error.message)
        assert exc_info.value.retry_after > 0

        mock_call_next.reset_mock()
        mock_context.message.name = "client_2"
        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await middleware.on_request(mock_context, mock_call_next)

        assert "Rate limit exceeded for client: client_2" in str(exc_info.value.error.message)

    async def test_multiple_limits_enforced(self, mock_context, mock_call_next, monkeypatch):
        """Test that multiple limits are enforced (per-tool + global)."""
        base_time = time.time()
        middleware = RateLimitingMiddleware(
            global_limit=5.0,
            per_tool_limit={"test_tool": 1.0},
            burst_capacity=1,
        )

        assert middleware._global_limiter is not None
        middleware._global_limiter.last_refill = base_time
        middleware._tool_limiters["test_tool"].last_refill = base_time

        await middleware.on_request(mock_context, mock_call_next)
        mock_call_next.reset_mock()

        monkeypatch.setattr(time, "time", lambda: base_time + 0.2)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await middleware.on_request(mock_context, mock_call_next)

        assert "Rate limit exceeded for tool: test_tool" in str(exc_info.value.error.message)

        monkeypatch.setattr(time, "time", lambda: base_time + 1.1)

        await middleware.on_request(mock_context, mock_call_next)
        assert mock_call_next.called

    def test_update_limits_global(self, mock_context):
        """Test updating global limit at runtime."""
        middleware = RateLimitingMiddleware(global_limit=10.0, burst_capacity=20)

        assert middleware._global_limiter is not None
        assert middleware._global_limiter.refill_rate == 10.0
        assert middleware._global_limiter.capacity == 20

        middleware.update_limits(global_limit=20.0)

        assert middleware.global_limit == 20.0
        assert middleware._global_limiter is not None
        assert middleware._global_limiter.refill_rate == 20.0
        assert middleware._global_limiter.capacity == 20

        middleware.update_limits(global_limit=30.0, burst_capacity=60)

        assert middleware.global_limit == 30.0
        assert middleware._global_limiter is not None
        assert middleware._global_limiter.refill_rate == 30.0
        assert middleware._global_limiter.capacity == 60

    def test_update_limits_per_tool(self, mock_context):
        """Test updating per-tool limits at runtime."""
        middleware = RateLimitingMiddleware(per_tool_limit={"old_tool": 5.0})

        assert "old_tool" in middleware._tool_limiters
        assert "new_tool" not in middleware._tool_limiters

        middleware.update_limits(per_tool_limit={"new_tool": 10.0})

        assert middleware.per_tool_limit == {"new_tool": 10.0}
        assert "old_tool" not in middleware._tool_limiters
        assert "new_tool" in middleware._tool_limiters
        assert middleware._tool_limiters["new_tool"].refill_rate == 10.0

    def test_update_limits_per_client(self, mock_context):
        """Test updating per-client limits at runtime."""
        middleware = RateLimitingMiddleware(per_client_limit=5.0)

        assert middleware.per_client_limit == 5.0
        assert middleware._client_limiters == {}

        middleware.update_limits(per_client_limit=10.0)

        assert middleware.per_client_limit == 10.0
        assert middleware._client_limiters == {}

    def test_update_limits_burst_capacity(self, mock_context):
        """Test updating burst capacity at runtime."""
        middleware = RateLimitingMiddleware(global_limit=10.0)

        assert middleware._global_limiter is not None
        assert middleware._global_limiter.capacity == 20

        middleware.update_limits(burst_capacity=50)

        assert middleware.burst_capacity == 50

        middleware.update_limits(global_limit=5.0)
        assert middleware._global_limiter is not None
        assert middleware._global_limiter.capacity == 50


class TestRateLimitDecorator:
    """Test @rate_limit decorator."""

    async def test_decorator_sync_function(self, monkeypatch):
        """Test decorator with synchronous function."""
        base_time = time.time()

        @rate_limit(qps=1.0, burst_capacity=1)
        def sync_func():
            return "sync_result"

        assert sync_func.__fastmcp_tool_limiter__ is not None
        sync_func.__fastmcp_tool_limiter__.last_refill = base_time

        result = await sync_func()
        assert result == "sync_result"

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await sync_func()

        assert "Rate limit exceeded for tool: sync_func" in str(exc_info.value.error.message)
        assert exc_info.value.retry_after > 0

    async def test_decorator_async_function(self, monkeypatch):
        """Test decorator with asynchronous function."""
        base_time = time.time()

        @rate_limit(qps=1.0, burst_capacity=1)
        async def async_func():
            await asyncio.sleep(0)
            return "async_result"

        assert async_func.__fastmcp_tool_limiter__ is not None
        async_func.__fastmcp_tool_limiter__.last_refill = base_time

        result = await async_func()
        assert result == "async_result"

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await async_func()

        assert "Rate limit exceeded for tool: async_func" in str(exc_info.value.error.message)

    async def test_decorator_recovers(self, monkeypatch):
        """Test that decorator allows requests again after waiting."""
        base_time = time.time()

        @rate_limit(qps=1.0, burst_capacity=1)
        def test_func():
            return "result"

        test_func.__fastmcp_tool_limiter__.last_refill = base_time

        await test_func()

        monkeypatch.setattr(time, "time", lambda: base_time + 1.1)

        result = await test_func()
        assert result == "result"

    async def test_decorator_multiple_applications(self):
        """Test applying decorator multiple times (stricter limit wins)."""

        @rate_limit(qps=10.0, burst_capacity=20)
        @rate_limit(qps=5.0, burst_capacity=10)
        def test_func():
            return "result"

        assert test_func.__fastmcp_tool_limiter__ is not None
        assert test_func.__fastmcp_tool_limiter__.refill_rate == 5.0
        assert test_func.__fastmcp_tool_limiter__.capacity == 10


@pytest.fixture
def rate_limit_server():
    """Create a FastMCP server specifically for rate limiting tests."""
    mcp = FastMCP("RateLimitTestServer")

    @mcp.tool
    def quick_action(message: str) -> str:
        """A quick action for testing rate limits."""
        return f"Processed: {message}"

    @mcp.tool
    def batch_process(items: list[str]) -> str:
        """Process multiple items."""
        return f"Processed {len(items)} items"

    @mcp.tool
    def heavy_computation() -> str:
        """A heavy computation that might need rate limiting."""
        return "Heavy computation complete"

    return mcp


class TestRateLimitingMiddlewareIntegration:
    """Integration tests for rate limiting middleware with real FastMCP server."""

    async def test_rate_limiting_allows_normal_usage(self, rate_limit_server):
        """Test that normal usage patterns are allowed through rate limiting."""
        rate_limit_server.add_middleware(
            RateLimitingMiddleware(global_limit=50.0, burst_capacity=20)
        )

        async with Client(rate_limit_server) as client:
            for i in range(3):
                result = await client.call_tool(
                    "quick_action", {"message": f"task_{i}"}
                )
                assert f"Processed: task_{i}" in str(result)

    async def test_rate_limiting_blocks_rapid_requests(self, rate_limit_server):
        """Test that rate limiting blocks rapid successive requests."""
        rate_limit_server.add_middleware(
            RateLimitingMiddleware(global_limit=0.001, burst_capacity=10)
        )

        async with Client(rate_limit_server) as client:
            hit_limit = False
            for i in range(15):
                try:
                    await client.call_tool("quick_action", {"message": str(i)})
                except ToolError as exc:
                    assert "Global rate limit exceeded" in str(exc) or "Rate limit exceeded" in str(exc)
                    hit_limit = True
                    break
            assert hit_limit, "Rate limit was never triggered"

    async def test_per_tool_rate_limiting(self, rate_limit_server):
        """Test per-tool rate limiting."""
        rate_limit_server.add_middleware(
            RateLimitingMiddleware(
                per_tool_limit={"quick_action": 0.001},
                burst_capacity=10,
            )
        )

        async with Client(rate_limit_server) as client:
            await client.call_tool("quick_action", {"message": "1"})

            with pytest.raises(ToolError) as exc_info:
                for i in range(10):
                    await client.call_tool("quick_action", {"message": str(i + 2)})

            assert "Rate limit exceeded for tool: quick_action" in str(exc_info.value)

            result = await client.call_tool("heavy_computation")
            assert "Heavy computation complete" in str(result)

    async def test_decorator_with_middleware(self, rate_limit_server):
        """Test that decorator and middleware can work together."""
        from fastmcp.server.middleware.rate_limiting import rate_limit as rate_limit_decorator

        mcp = FastMCP("DecoratorTestServer")

        @mcp.tool
        @rate_limit_decorator(qps=0.001, burst_capacity=10)
        def limited_tool() -> str:
            return "Limited tool result"

        @mcp.tool
        def unlimited_tool() -> str:
            return "Unlimited tool result"

        mcp.add_middleware(RateLimitingMiddleware(global_limit=100.0, burst_capacity=100))

        async with Client(mcp) as client:
            await client.call_tool("limited_tool")

            with pytest.raises(ToolError) as exc_info:
                for i in range(10):
                    await client.call_tool("limited_tool")

            assert "Rate limit exceeded for tool: limited_tool" in str(exc_info.value)

            result = await client.call_tool("unlimited_tool")
            assert "Unlimited tool result" in str(result)

    async def test_hot_update_integration(self, rate_limit_server):
        """Test that hot updates work in integration."""
        middleware = RateLimitingMiddleware(global_limit=0.001, burst_capacity=5)
        rate_limit_server.add_middleware(middleware)

        async with Client(rate_limit_server) as client:
            await client.call_tool("quick_action", {"message": "1"})

            hit_limit = False
            for i in range(10):
                try:
                    await client.call_tool("quick_action", {"message": str(i + 2)})
                except ToolError:
                    hit_limit = True
                    break
            assert hit_limit, "Rate limit was never triggered before update"

            middleware.update_limits(global_limit=100.0)

            result = await client.call_tool("quick_action", {"message": "after_update"})
            assert "Processed: after_update" in str(result)

    async def test_retry_after_in_error_message(self, rate_limit_server):
        """Test that retry_after is included in error messages."""
        middleware = RateLimitingMiddleware(global_limit=0.001, burst_capacity=5)
        rate_limit_server.add_middleware(middleware)

        async with Client(rate_limit_server) as client:
            await client.call_tool("quick_action", {"message": "1"})

            with pytest.raises(ToolError) as exc_info:
                for i in range(10):
                    await client.call_tool("quick_action", {"message": str(i + 2)})

            error_str = str(exc_info.value)
            assert "Global rate limit exceeded" in error_str
            assert "Retry after" in error_str


class TestInMemoryBackend:
    """Test InMemoryBackend implementation."""

    async def test_try_consume_success(self):
        """Test successful token consumption."""
        backend = InMemoryBackend()

        allowed = await backend.try_consume(
            key="test_key",
            qps=10.0,
            burst=20,
            tokens=1,
        )
        assert allowed is True

    async def test_try_consume_failure(self, monkeypatch):
        """Test failed token consumption when rate limit is exceeded."""
        backend = InMemoryBackend()
        base_time = time.time()

        for i in range(5):
            allowed = await backend.try_consume(
                key="test_key",
                qps=1.0,
                burst=5,
                tokens=1,
            )
            assert allowed is True

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        allowed = await backend.try_consume(
            key="test_key",
            qps=1.0,
            burst=5,
            tokens=1,
        )
        assert allowed is False

    async def test_get_wait_time_available(self):
        """Test get_wait_time when tokens are available."""
        backend = InMemoryBackend()

        wait_time = await backend.get_wait_time(
            key="test_key",
            qps=10.0,
            burst=20,
            tokens=1,
        )
        assert wait_time == 0.0

    async def test_get_wait_time_needed(self, monkeypatch):
        """Test get_wait_time when tokens need to be refilled."""
        backend = InMemoryBackend()
        base_time = time.time()

        for i in range(5):
            await backend.try_consume(
                key="test_key",
                qps=1.0,
                burst=5,
                tokens=1,
            )

        wait_time = await backend.get_wait_time(
            key="test_key",
            qps=1.0,
            burst=5,
            tokens=1,
        )
        assert wait_time == pytest.approx(1.0, abs=0.01)

    async def test_multiple_keys_independent(self):
        """Test that different keys have independent rate limits."""
        backend = InMemoryBackend()

        for i in range(5):
            await backend.try_consume(
                key="key_1",
                qps=1.0,
                burst=5,
                tokens=1,
            )

        for i in range(5):
            allowed = await backend.try_consume(
                key="key_2",
                qps=1.0,
                burst=5,
                tokens=1,
            )
            assert allowed is True


class TestRateLimitExceededErrorAttributes:
    """Test additional attributes of RateLimitExceededError."""

    def test_init_with_all_attributes(self):
        """Test initialization with all optional attributes."""
        error = RateLimitExceededError(
            message="Custom message",
            retry_after=5.0,
            client_id="test_client",
            tool_name="test_tool",
            limit_type="global",
        )

        assert error.retry_after == 5.0
        assert error.client_id == "test_client"
        assert error.tool_name == "test_tool"
        assert error.limit_type == "global"

    def test_init_with_default_attributes(self):
        """Test initialization with default attributes."""
        error = RateLimitExceededError()

        assert error.retry_after == 1.0
        assert error.client_id is None
        assert error.tool_name is None
        assert error.limit_type is None


class TestStrictModeClientId:
    """Test strict_mode behavior for client ID resolution."""

    async def test_strict_mode_none_client_id(self, mock_context, mock_call_next):
        """Test that strict_mode raises error when get_client_id returns None."""
        def get_client_id(ctx):
            return None

        middleware = RateLimitingMiddleware(
            per_client_limit=10.0,
            get_client_id=get_client_id,
            strict_mode=True,
        )

        with pytest.raises(RateLimitExceededError) as exc_info:
            await middleware.on_request(mock_context, mock_call_next)

        assert "Client ID resolution failed" in str(exc_info.value.error.message)
        assert exc_info.value.limit_type == "per_client"

    async def test_strict_mode_exception_client_id(self, mock_context, mock_call_next):
        """Test that strict_mode raises error when get_client_id raises exception."""
        def get_client_id(ctx):
            raise ValueError("Failed to get client ID")

        middleware = RateLimitingMiddleware(
            per_client_limit=10.0,
            get_client_id=get_client_id,
            strict_mode=True,
        )

        with pytest.raises(RateLimitExceededError) as exc_info:
            await middleware.on_request(mock_context, mock_call_next)

        assert "Client ID resolution failed" in str(exc_info.value.error.message)
        assert exc_info.value.limit_type == "per_client"

    async def test_non_strict_mode_none_client_id(self, mock_context, mock_call_next):
        """Test that non-strict_mode uses 'anonymous' when get_client_id returns None."""
        def get_client_id(ctx):
            return None

        middleware = RateLimitingMiddleware(
            per_client_limit=10.0,
            get_client_id=get_client_id,
            strict_mode=False,
        )

        await middleware.on_request(mock_context, mock_call_next)
        assert mock_call_next.called
        assert "anonymous" in middleware._client_limiters

    async def test_non_strict_mode_exception_client_id(self, mock_context, mock_call_next):
        """Test that non-strict_mode uses 'anonymous' when get_client_id raises exception."""
        def get_client_id(ctx):
            raise ValueError("Failed to get client ID")

        middleware = RateLimitingMiddleware(
            per_client_limit=10.0,
            get_client_id=get_client_id,
            strict_mode=False,
        )

        await middleware.on_request(mock_context, mock_call_next)
        assert mock_call_next.called
        assert "anonymous" in middleware._client_limiters


class TestGetStats:
    """Test get_stats() method for rate limiting statistics."""

    def test_get_stats_initial(self):
        """Test initial state of get_stats()."""
        middleware = RateLimitingMiddleware()

        stats = middleware.get_stats()
        assert stats["total_requests"] == 0
        assert stats["accepted"] == 0
        assert stats["rejected"] == 0
        assert stats["rejected_by_rule"]["global"] == 0
        assert stats["rejected_by_rule"]["per_tool"] == 0
        assert stats["rejected_by_rule"]["per_client"] == 0

    async def test_get_stats_accepted(self, mock_context, mock_call_next):
        """Test that accepted requests are counted."""
        middleware = RateLimitingMiddleware(global_limit=100.0, burst_capacity=100)

        for i in range(5):
            await middleware.on_request(mock_context, mock_call_next)
            mock_call_next.reset_mock()

        stats = middleware.get_stats()
        assert stats["total_requests"] == 5
        assert stats["accepted"] == 5
        assert stats["rejected"] == 0

    async def test_get_stats_rejected_global(self, mock_context, mock_call_next, monkeypatch):
        """Test that rejected requests are counted with breakdown."""
        base_time = time.time()
        middleware = RateLimitingMiddleware(global_limit=1.0, burst_capacity=1)

        assert middleware._global_limiter is not None
        middleware._global_limiter.last_refill = base_time

        await middleware.on_request(mock_context, mock_call_next)
        mock_call_next.reset_mock()

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError):
            await middleware.on_request(mock_context, mock_call_next)

        stats = middleware.get_stats()
        assert stats["total_requests"] == 2
        assert stats["accepted"] == 1
        assert stats["rejected"] == 1
        assert stats["rejected_by_rule"]["global"] == 1
        assert stats["rejected_by_rule"]["per_tool"] == 0
        assert stats["rejected_by_rule"]["per_client"] == 0

    async def test_get_stats_rejected_per_tool(self, mock_context, mock_call_next, monkeypatch):
        """Test that per-tool rejected requests are counted."""
        base_time = time.time()
        middleware = RateLimitingMiddleware(
            per_tool_limit={"test_tool": 1.0},
            burst_capacity=1,
        )

        middleware._tool_limiters["test_tool"].last_refill = base_time

        await middleware.on_request(mock_context, mock_call_next)
        mock_call_next.reset_mock()

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError):
            await middleware.on_request(mock_context, mock_call_next)

        stats = middleware.get_stats()
        assert stats["rejected"] == 1
        assert stats["rejected_by_rule"]["per_tool"] == 1

    async def test_get_stats_rejected_per_client(self, mock_context, mock_call_next, monkeypatch):
        """Test that per-client rejected requests are counted."""
        base_time = time.time()
        middleware = RateLimitingMiddleware(
            per_client_limit=1.0,
            burst_capacity=1,
        )

        await middleware.on_request(mock_context, mock_call_next)
        mock_call_next.reset_mock()

        assert "default" in middleware._client_limiters
        middleware._client_limiters["default"].last_refill = base_time

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError):
            await middleware.on_request(mock_context, mock_call_next)

        stats = middleware.get_stats()
        assert stats["rejected"] == 1
        assert stats["rejected_by_rule"]["per_client"] == 1

    def test_reset_stats(self):
        """Test reset_stats() method."""
        middleware = RateLimitingMiddleware()

        middleware._stats["total_requests"] = 100
        middleware._stats["accepted"] = 90
        middleware._stats["rejected"] = 10
        middleware._stats["rejected_by_rule"]["global"] = 5

        middleware.reset_stats()

        stats = middleware.get_stats()
        assert stats["total_requests"] == 0
        assert stats["accepted"] == 0
        assert stats["rejected"] == 0
        assert stats["rejected_by_rule"]["global"] == 0


class TestCustomCounters:
    """Test custom RateLimitCounters integration."""

    async def test_custom_counters_accepted(self, mock_context, mock_call_next):
        """Test that custom counters are called for accepted requests."""
        class TestCounters:
            def __init__(self):
                self.accepted_calls = []
                self.rejected_calls = []

            def increment_accepted(self, limit_type: str | None = None):
                self.accepted_calls.append(limit_type)

            def increment_rejected(self, limit_type: str):
                self.rejected_calls.append(limit_type)

        counters = TestCounters()
        middleware = RateLimitingMiddleware(
            global_limit=100.0,
            burst_capacity=100,
            counters=counters,
        )

        await middleware.on_request(mock_context, mock_call_next)

        assert len(counters.accepted_calls) == 1
        assert len(counters.rejected_calls) == 0

    async def test_custom_counters_rejected(self, mock_context, mock_call_next, monkeypatch):
        """Test that custom counters are called for rejected requests."""
        class TestCounters:
            def __init__(self):
                self.accepted_calls = []
                self.rejected_calls = []

            def increment_accepted(self, limit_type: str | None = None):
                self.accepted_calls.append(limit_type)

            def increment_rejected(self, limit_type: str):
                self.rejected_calls.append(limit_type)

        counters = TestCounters()
        base_time = time.time()
        middleware = RateLimitingMiddleware(
            global_limit=1.0,
            burst_capacity=1,
            counters=counters,
        )

        assert middleware._global_limiter is not None
        middleware._global_limiter.last_refill = base_time

        await middleware.on_request(mock_context, mock_call_next)
        mock_call_next.reset_mock()

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError):
            await middleware.on_request(mock_context, mock_call_next)

        assert len(counters.accepted_calls) == 1
        assert len(counters.rejected_calls) == 1
        assert counters.rejected_calls[0] == "global"


class TestErrorMessageTemplate:
    """Test custom error message templates."""

    async def test_custom_error_message_template(self, mock_context, mock_call_next, monkeypatch):
        """Test that custom error message templates are applied."""
        base_time = time.time()
        middleware = RateLimitingMiddleware(
            global_limit=1.0,
            burst_capacity=1,
            error_message_template=(
                "Custom message: client={client_id}, "
                "tool={tool_name}, type={limit_type}, "
                "wait={retry_after:.2f}"
            ),
        )

        assert middleware._global_limiter is not None
        middleware._global_limiter.last_refill = base_time

        await middleware.on_request(mock_context, mock_call_next)
        mock_call_next.reset_mock()

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await middleware.on_request(mock_context, mock_call_next)

        error_msg = str(exc_info.value.error.message)
        assert "client=default" in error_msg
        assert "tool=test_tool" in error_msg
        assert "type=global" in error_msg
        assert "wait=" in error_msg

    async def test_invalid_error_message_template(self, mock_context, mock_call_next, monkeypatch):
        """Test that invalid error message templates fall back to base message."""
        base_time = time.time()
        middleware = RateLimitingMiddleware(
            global_limit=1.0,
            burst_capacity=1,
            error_message_template="Invalid template: {nonexistent_key}",
        )

        assert middleware._global_limiter is not None
        middleware._global_limiter.last_refill = base_time

        await middleware.on_request(mock_context, mock_call_next)
        mock_call_next.reset_mock()

        monkeypatch.setattr(time, "time", lambda: base_time + 0.5)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await middleware.on_request(mock_context, mock_call_next)

        error_msg = str(exc_info.value.error.message)
        assert "Global rate limit exceeded" in error_msg


class TestRateLimitStatsIntegration:
    """Integration tests for rate limiting statistics."""

    async def test_stats_updated_in_integration(self, rate_limit_server):
        """Test that get_stats() is correctly updated in integration."""
        middleware = RateLimitingMiddleware(global_limit=10.0, burst_capacity=20)
        rate_limit_server.add_middleware(middleware)

        async with Client(rate_limit_server) as client:
            for i in range(5):
                await client.call_tool("quick_action", {"message": str(i)})

            stats = middleware.get_stats()
            assert stats["total_requests"] > 0
            assert stats["accepted"] > 0
            assert stats["rejected"] == 0
            assert stats["rejected_by_rule"]["global"] == 0

    async def test_reset_stats_integration(self, rate_limit_server):
        """Test that reset_stats() works in integration."""
        middleware = RateLimitingMiddleware(global_limit=10.0, burst_capacity=20)
        rate_limit_server.add_middleware(middleware)

        async with Client(rate_limit_server) as client:
            for i in range(3):
                await client.call_tool("quick_action", {"message": str(i)})

            stats_before = middleware.get_stats()
            assert stats_before["total_requests"] > 0

            middleware.reset_stats()

            stats_after = middleware.get_stats()
            assert stats_after["total_requests"] == 0
            assert stats_after["accepted"] == 0
            assert stats_after["rejected"] == 0
