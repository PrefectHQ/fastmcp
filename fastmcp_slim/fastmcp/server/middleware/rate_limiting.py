"""Rate limiting middleware for protecting FastMCP servers from abuse."""

import inspect
import time
import warnings
from collections import OrderedDict, defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar, cast

import anyio
from mcp import MCPError

from fastmcp._warnings import FastMCPDeprecationWarning

from .middleware import CallNext, Middleware, MiddlewareContext

LimiterT = TypeVar("LimiterT")


class _PerClientLimiterCache(Generic[LimiterT]):
    """Lazily retain per-client limiters and discard semantically idle entries."""

    _MAX_PRUNED_PER_REQUEST = 64

    def __init__(
        self, factory: Callable[[], LimiterT], *, idle_timeout: float | None
    ) -> None:
        self._factory = factory
        self._idle_timeout = idle_timeout
        self.limiters: defaultdict[str, LimiterT] = defaultdict(factory)
        self._last_used: OrderedDict[str, float] = OrderedDict()
        self._active_requests: dict[str, int] = {}
        self._lock = anyio.Lock()

    def _prune_idle(self, now: float) -> None:
        if self._idle_timeout is None:
            return

        cutoff = now - self._idle_timeout
        for _ in range(self._MAX_PRUNED_PER_REQUEST):
            if not self._last_used:
                break

            client_id, last_used = next(iter(self._last_used.items()))
            if self._active_requests.get(client_id, 0):
                self._last_used[client_id] = now
                self._last_used.move_to_end(client_id)
                continue
            if last_used >= cutoff:
                break

            self._last_used.popitem(last=False)
            self.limiters.pop(client_id, None)

    async def acquire(self, client_id: str) -> LimiterT:
        async with self._lock:
            now = time.time()
            self._prune_idle(now)

            limiter = self.limiters.get(client_id)
            if limiter is None:
                limiter = self._factory()
                self.limiters[client_id] = limiter

            self._last_used[client_id] = now
            self._last_used.move_to_end(client_id)
            self._active_requests[client_id] = (
                self._active_requests.get(client_id, 0) + 1
            )
            return limiter

    async def release(self, client_id: str) -> None:
        # A cancelled request must not leave a permanent active reference that
        # prevents the corresponding limiter from being reclaimed.
        with anyio.CancelScope(shield=True):
            async with self._lock:
                self._last_used[client_id] = time.time()
                self._last_used.move_to_end(client_id)
                active = self._active_requests.get(client_id, 0)
                if active <= 1:
                    self._active_requests.pop(client_id, None)
                else:
                    self._active_requests[client_id] = active - 1


class RateLimitError(MCPError):
    """Error raised when rate limit is exceeded."""

    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(code=-32000, message=message)


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
        get_client_id: Callable[[MiddlewareContext], str]
        | Callable[[MiddlewareContext], Awaitable[str]]
        | None = None,
        global_limit: bool = False,
    ):
        """Initialize rate limiting middleware.

        Args:
            max_requests_per_second: Sustained requests per second allowed
            burst_capacity: Maximum burst capacity, at least 1. If None or 0, defaults to
                2x max_requests_per_second (at least 1 for a positive rate). Values below 1
                are deprecated.
            get_client_id: Function to extract client ID from context. Can be sync or async.
                If None, uses global limiting
            global_limit: If True, apply limit globally; if False, per-client
        """
        if burst_capacity is not None and burst_capacity < 1:
            if max_requests_per_second > 0:
                instead = (
                    "Pass burst_capacity=1 for no bursting beyond the steady rate."
                )
            else:
                instead = (
                    "To reject every request, omit burst_capacity: with "
                    "max_requests_per_second=0 the default capacity is 0."
                )
            warnings.warn(
                f"RateLimitingMiddleware(burst_capacity={burst_capacity!r}) is deprecated "
                "and will raise a ValueError in FastMCP 5: a bucket that holds less than "
                "one token can't admit a request, so 0 falls back to the default and other "
                f"values reject every request. {instead}",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        self.max_requests_per_second = max_requests_per_second
        default_capacity = int(max_requests_per_second * 2)
        if max_requests_per_second > 0:
            # A positive rate always admits at least one request; a zero rate
            # keeps its capacity of 0, which rejects every request.
            default_capacity = max(1, default_capacity)
        self.burst_capacity = burst_capacity or default_capacity
        self.get_client_id = get_client_id
        self.global_limit = global_limit

        # Once idle for long enough, a token bucket is full again. Preserve
        # buckets with a positive capacity and no refill rate because they can
        # remain depleted indefinitely.
        if self.burst_capacity <= 0:
            idle_timeout: float | None = 0
        elif self.max_requests_per_second > 0:
            idle_timeout = self.burst_capacity / self.max_requests_per_second
        else:
            idle_timeout = None

        self._client_limiters = _PerClientLimiterCache(
            lambda: TokenBucketRateLimiter(
                self.burst_capacity, self.max_requests_per_second
            ),
            idle_timeout=idle_timeout,
        )
        self.limiters = self._client_limiters.limiters

        # Global rate limiter
        if self.global_limit:
            self.global_limiter = TokenBucketRateLimiter(
                self.burst_capacity, self.max_requests_per_second
            )

    async def _get_client_identifier(self, context: MiddlewareContext) -> str:
        """Get client identifier for rate limiting."""
        if self.get_client_id:
            client_id = self.get_client_id(context)
            if inspect.isawaitable(client_id):
                return cast(str, await client_id)
            return client_id
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
            client_id = await self._get_client_identifier(context)
            limiter = await self._client_limiters.acquire(client_id)
            try:
                allowed = await limiter.consume()
            finally:
                await self._client_limiters.release(client_id)
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
        get_client_id: Callable[[MiddlewareContext], str]
        | Callable[[MiddlewareContext], Awaitable[str]]
        | None = None,
    ):
        """Initialize sliding window rate limiting middleware.

        Args:
            max_requests: Maximum requests allowed in the time window
            window_minutes: Time window in minutes
            get_client_id: Function to extract client ID from context. Can be sync or async.
                If None, uses global limiting
        """
        self.max_requests = max_requests
        self.window_seconds = window_minutes * 60
        self.get_client_id = get_client_id

        # Once no request has used a sliding window for its full duration, all
        # timestamps it could contain have expired and its state is empty.
        self._client_limiters = _PerClientLimiterCache(
            lambda: SlidingWindowRateLimiter(self.max_requests, self.window_seconds),
            idle_timeout=max(0, self.window_seconds),
        )
        self.limiters = self._client_limiters.limiters

    async def _get_client_identifier(self, context: MiddlewareContext) -> str:
        """Get client identifier for rate limiting."""
        if self.get_client_id:
            client_id = self.get_client_id(context)
            if inspect.isawaitable(client_id):
                return cast(str, await client_id)
            return client_id
        return "global"

    async def on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Apply sliding window rate limiting to requests."""
        client_id = await self._get_client_identifier(context)
        limiter = await self._client_limiters.acquire(client_id)
        try:
            allowed = await limiter.is_allowed()
        finally:
            await self._client_limiters.release(client_id)
        if not allowed:
            raise RateLimitError(
                f"Rate limit exceeded: {self.max_requests} requests per "
                f"{self.window_seconds // 60} minutes for client: {client_id}"
            )

        return await call_next(context)
