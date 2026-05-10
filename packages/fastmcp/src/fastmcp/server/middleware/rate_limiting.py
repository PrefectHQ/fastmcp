"""Rate limiting middleware for protecting FastMCP servers from abuse."""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

import anyio
from mcp import McpError
from mcp.types import ErrorData

from .middleware import CallNext, Middleware, MiddlewareContext


class RateLimitError(McpError):
    """Error raised when rate limit is exceeded."""

    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(ErrorData(code=-32000, message=message))


class TokenBucketRateLimiter:
    """Token bucket implementation for rate limiting."""

    def __init__(self, capacity: int, refill_rate: float):
        """Initialize token bucket.

        Args:
            capacity: Maximum number of tokens in the bucket
            refill_rate: Tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = time.time()
        self._lock = anyio.Lock()

    async def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens from the bucket.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were available and consumed, False otherwise
        """
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_refill

            # Add tokens based on elapsed time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False


class SlidingWindowRateLimiter:
    """Sliding window rate limiter implementation."""

    def __init__(self, max_requests: int, window_seconds: int):
        """Initialize sliding window rate limiter.

        Args:
            max_requests: Maximum requests allowed in the time window
            window_seconds: Time window in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = deque()
        self._lock = anyio.Lock()

    async def is_allowed(self) -> bool:
        """Check if a request is allowed."""
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds

            # Remove old requests outside the window
            while self.requests and self.requests[0] < cutoff:
                self.requests.popleft()

            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True
            return False


class RateLimitingMiddleware(Middleware):
    """Middleware that implements rate limiting to prevent server abuse.

    Uses a token bucket algorithm by default, allowing for burst traffic
    while maintaining a sustainable long-term rate.

    Example:
        ```python
        from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

        # Allow 10 requests per second with bursts up to 20
        rate_limiter = RateLimitingMiddleware(
            max_requests_per_second=10,
            burst_capacity=20
        )

        mcp = FastMCP("MyServer")
        mcp.add_middleware(rate_limiter)
        ```
    """

    def __init__(
        self,
        max_requests_per_second: float = 10.0,
        burst_capacity: int | None = None,
        get_client_id: Callable[[MiddlewareContext], str] | None = None,
        global_limit: bool = False,
    ):
        """Initialize rate limiting middleware.

        Args:
            max_requests_per_second: Sustained requests per second allowed
            burst_capacity: Maximum burst capacity. If None, defaults to 2x max_requests_per_second
            get_client_id: Function to extract client ID from context. If None, uses global limiting
            global_limit: If True, apply limit globally; if False, per-client
        """
        self.max_requests_per_second = max_requests_per_second
        self.burst_capacity = burst_capacity or int(max_requests_per_second * 2)
        self.get_client_id = get_client_id
        self.global_limit = global_limit

        # Storage for rate limiters per client
        self.limiters: dict[str, TokenBucketRateLimiter] = defaultdict(
            lambda: TokenBucketRateLimiter(
                self.burst_capacity, self.max_requests_per_second
            )
        )

        # Global rate limiter
        if self.global_limit:
            self.global_limiter = TokenBucketRateLimiter(
                self.burst_capacity, self.max_requests_per_second
            )

    def _get_client_identifier(self, context: MiddlewareContext) -> str:
        """Get client identifier for rate limiting."""
        if self.get_client_id:
            return self.get_client_id(context)
        return "global"

    async def on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Apply rate limiting to requests."""
        if self.global_limit:
            # Global rate limiting
            allowed = await self.global_limiter.consume()
            if not allowed:
                raise RateLimitError("Global rate limit exceeded")
        else:
            # Per-client rate limiting
            client_id = self._get_client_identifier(context)
            limiter = self.limiters[client_id]
            allowed = await limiter.consume()
            if not allowed:
                raise RateLimitError(f"Rate limit exceeded for client: {client_id}")

        return await call_next(context)


class SlidingWindowRateLimitingMiddleware(Middleware):
    """Middleware that implements sliding window rate limiting.

    Uses a sliding window approach which provides more precise rate limiting
    but uses more memory to track individual request timestamps.

    Example:
        ```python
        from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware

        # Allow 100 requests per minute
        rate_limiter = SlidingWindowRateLimitingMiddleware(
            max_requests=100,
            window_minutes=1
        )

        mcp = FastMCP("MyServer")
        mcp.add_middleware(rate_limiter)
        ```
    """

    def __init__(
        self,
        max_requests: int,
        window_minutes: int = 1,
        get_client_id: Callable[[MiddlewareContext], str] | None = None,
    ):
        """Initialize sliding window rate limiting middleware.

        Args:
            max_requests: Maximum requests allowed in the time window
            window_minutes: Time window in minutes
            get_client_id: Function to extract client ID from context
        """
        self.max_requests = max_requests
        self.window_seconds = window_minutes * 60
        self.get_client_id = get_client_id

        # Storage for rate limiters per client
        self.limiters: dict[str, SlidingWindowRateLimiter] = defaultdict(
            lambda: SlidingWindowRateLimiter(self.max_requests, self.window_seconds)
        )

    def _get_client_identifier(self, context: MiddlewareContext) -> str:
        """Get client identifier for rate limiting."""
        if self.get_client_id:
            return self.get_client_id(context)
        return "global"

    async def on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Apply sliding window rate limiting to requests."""
        client_id = self._get_client_identifier(context)
        limiter = self.limiters[client_id]

        allowed = await limiter.is_allowed()
        if not allowed:
            raise RateLimitError(
                f"Rate limit exceeded: {self.max_requests} requests per "
                f"{self.window_seconds // 60} minutes for client: {client_id}"
            )

        return await call_next(context)
