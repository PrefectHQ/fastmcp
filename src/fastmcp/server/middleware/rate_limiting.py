"""Rate limiting middleware for protecting FastMCP servers from abuse.

This module provides a comprehensive rate limiting solution with:
- Multiple rate limiting strategies (token bucket, sliding window)
- Flexible configuration (global, per-tool, per-client)
- Hot reconfiguration support
- Metrics and monitoring hooks
- Extensible backend architecture (in-memory, Redis, etc.)

## Quick Start

```python
from fastmcp import FastMCP
from fastmcp.server.middleware.rate_limiting import (
    RateLimitingMiddleware,
    rate_limit,
)

mcp = FastMCP("MyServer")

# Global rate limiting: 100 QPS with burst capacity of 200
mcp.add_middleware(RateLimitingMiddleware(global_limit=100.0, burst_capacity=200))

# Per-tool rate limiting
mcp.add_middleware(RateLimitingMiddleware(
    per_tool_limit={"search": 10.0, "write": 2.0},
))

# Per-client rate limiting
def get_client_id(context):
    # Extract client ID from request context
    return "client_123"

mcp.add_middleware(RateLimitingMiddleware(
    per_client_limit=50.0,
    get_client_id=get_client_id,
))

# Decorator for individual tools
@mcp.tool
@rate_limit(qps=10.0, burst_capacity=20)
def search(query: str) -> str:
    return f"Results for: {query}"
```

## Backend Architecture

The rate limiting system uses a backend abstraction that allows
swapping between different implementations:

- `InMemoryBackend`: Default, for single-server deployments
- `RedisBackend`: For distributed deployments (extendable)

Example using custom backend:
```python
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from my_redis_backend import RedisBackend

redis_backend = RedisBackend(redis_url="redis://localhost:6379/0")
mcp.add_middleware(RateLimitingMiddleware(
    global_limit=100.0,
    backend=redis_backend,
))
```

## Metrics and Monitoring

Get rate limiting statistics:
```python
middleware = RateLimitingMiddleware(global_limit=100.0)
stats = middleware.get_stats()
# {
#     "total_requests": 1000,
#     "accepted": 950,
#     "rejected": 50,
#     "rejected_by_rule": {
#         "global": 20,
#         "per_tool": 15,
#         "per_client": 15,
#     }
# }
```

## Custom Error Messages

Customize error message templates:
```python
middleware = RateLimitingMiddleware(
    global_limit=100.0,
    error_message_template=(
        "Rate limit exceeded for {client_id} "
        "calling {tool_name}. "
        "Retry after {retry_after:.2f} seconds."
    ),
)
```
"""

from __future__ import annotations

import inspect
import logging
import math
import time
from collections import defaultdict, deque
from collections.abc import Callable
from functools import wraps
from typing import Any, Protocol

import anyio
from mcp import McpError
from mcp.types import ErrorData

from ._rate_limit_backend import InMemoryBackend, RateLimitBackend
from .middleware import CallNext, Middleware, MiddlewareContext

logger = logging.getLogger(__name__)


class RateLimitExceededError(McpError):
    """Error raised when rate limit is exceeded.

    Inherits from McpError and includes retry_after information for clients.

    Attributes:
        retry_after: Number of seconds to wait before retrying
        client_id: Client identifier that triggered the limit (if available)
        tool_name: Tool name that triggered the limit (if available)
        limit_type: Type of limit that was exceeded ("global", "per_tool", "per_client")
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: float = 1.0,
        client_id: str | None = None,
        tool_name: str | None = None,
        limit_type: str | None = None,
    ):
        """Initialize rate limit error.

        Args:
            message: Error message
            retry_after: Number of seconds to wait before retrying
            client_id: Client identifier that triggered the limit
            tool_name: Tool name that triggered the limit
            limit_type: Type of limit that was exceeded
        """
        self.retry_after = retry_after
        self.client_id = client_id
        self.tool_name = tool_name
        self.limit_type = limit_type

        full_message = f"{message}. Retry after {retry_after:.2f} seconds."
        super().__init__(ErrorData(code=-32000, message=full_message))


RateLimitError = RateLimitExceededError


class RateLimitStats(Protocol):
    """Protocol for rate limit statistics.

    Attributes:
        total_requests: Total number of requests processed
        accepted: Number of requests that passed rate limiting
        rejected: Number of requests that were rate limited
        rejected_by_rule: Breakdown of rejected requests by limit type
    """

    total_requests: int
    accepted: int
    rejected: int
    rejected_by_rule: dict[str, int]


class RateLimitCounters(Protocol):
    """Protocol for Prometheus-style counters.

    Users can implement this protocol to send metrics to
    Prometheus, StatsD, or any monitoring system.

    Example:
        ```python
        class MyPrometheusCounters:
            def __init__(self):
                self.requests_total = Counter(
                    "fastmcp_rate_limit_requests_total",
                    "Total rate limited requests",
                    ["status", "limit_type"],
                )

            def increment_accepted(self, limit_type: str | None = None):
                self.requests_total.labels(status="accepted", limit_type=limit_type or "none").inc()

            def increment_rejected(self, limit_type: str):
                self.requests_total.labels(status="rejected", limit_type=limit_type).inc()
        ```
    """

    def increment_accepted(self, limit_type: str | None = None) -> None:
        """Increment counter for accepted requests.

        Args:
            limit_type: Type of limit that was checked ("global", "per_tool", "per_client", or None)
        """
        ...

    def increment_rejected(self, limit_type: str) -> None:
        """Increment counter for rejected requests.

        Args:
            limit_type: Type of limit that was exceeded ("global", "per_tool", "per_client")
        """
        ...


class DefaultRateLimitCounters:
    """Default implementation of RateLimitCounters that does nothing.

    This is used when no custom counters are provided.
    """

    def increment_accepted(self, limit_type: str | None = None) -> None:
        """Do nothing for accepted requests."""
        pass

    def increment_rejected(self, limit_type: str) -> None:
        """Do nothing for rejected requests."""
        pass


class TokenBucketRateLimiter:
    """Token bucket implementation for rate limiting.

    Deprecated: Use InMemoryBackend instead.

    This class is kept for backwards compatibility with existing code.
    New code should use the backend abstraction via RateLimitBackend.

    The token bucket algorithm allows for burst traffic while maintaining
    a sustainable long-term rate. Tokens are added to the bucket at a
    fixed rate (refill_rate) up to a maximum capacity. Each request
    consumes one token.
    """

    def __init__(self, capacity: int, refill_rate: float):
        """Initialize token bucket.

        Args:
            capacity: Maximum number of tokens in the bucket (burst capacity)
            refill_rate: Tokens added per second (sustained rate)
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
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
            self._refill()

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def _refill(self) -> None:
        """Refill tokens based on elapsed time since last refill."""
        now = time.time()
        elapsed = now - self.last_refill

        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def get_wait_time(self, tokens: int = 1) -> float:
        """Calculate the time needed to wait before tokens are available.

        Args:
            tokens: Number of tokens needed

        Returns:
            Number of seconds to wait, or 0 if tokens are available
        """
        self._refill()

        if self.tokens >= tokens:
            return 0.0

        tokens_needed = tokens - self.tokens
        wait_time = tokens_needed / self.refill_rate if self.refill_rate > 0 else float("inf")
        return wait_time


class SlidingWindowRateLimiter:
    """Sliding window rate limiter implementation.

    Uses a sliding window approach which provides more precise rate limiting
    but uses more memory to track individual request timestamps.
    """

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
        """Check if a request is allowed.

        Returns:
            True if request is allowed, False otherwise
        """
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds

            while self.requests and self.requests[0] < cutoff:
                self.requests.popleft()

            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True
            return False

    def get_wait_time(self) -> float:
        """Calculate the time needed to wait before a request is allowed.

        Returns:
            Number of seconds to wait, or 0 if request is allowed
        """
        now = time.time()
        cutoff = now - self.window_seconds

        while self.requests and self.requests[0] < cutoff:
            self.requests.popleft()

        if len(self.requests) < self.max_requests:
            return 0.0

        oldest_request = self.requests[0]
        wait_time = self.window_seconds - (now - oldest_request)
        return max(0.0, wait_time)


class RateLimitingMiddleware(Middleware):
    """Middleware that implements rate limiting to prevent server abuse.

    Uses a token bucket algorithm by default, allowing for burst traffic
    while maintaining a sustainable long-term rate.

    Supports:
    - Global rate limiting (across all requests)
    - Per-client rate limiting (based on client_id from context)
    - Per-tool rate limiting (different limits for different tools)
    - Hot reconfiguration (update limits without restarting)
    - Metrics and monitoring (get_stats() and custom counters)
    - Extensible backend architecture (in-memory, Redis, etc.)
    - Custom error message templates
    - Per-client error handling (strict_mode for client ID resolution)

    Example:
        ```python
        from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

        # Allow 100 requests per second with bursts up to 200
        rate_limiter = RateLimitingMiddleware(
            global_limit=100.0,
            burst_capacity=200,
        )

        mcp = FastMCP("MyServer")
        mcp.add_middleware(rate_limiter)
        ```
    """

    def __init__(
        self,
        global_limit: float | None = None,
        burst_capacity: int | None = None,
        per_tool_limit: dict[str, float] | None = None,
        per_client_limit: float | None = None,
        get_client_id: Callable[[MiddlewareContext], str] | None = None,
        strict_mode: bool = False,
        backend: RateLimitBackend | None = None,
        counters: RateLimitCounters | None = None,
        error_message_template: str | None = None,
    ):
        """Initialize rate limiting middleware.

        Args:
            global_limit: Global requests per second limit (across all clients/tools)
            burst_capacity: Maximum burst capacity. If None, defaults to 2x the rate
            per_tool_limit: Dictionary mapping tool names to their QPS limits
            per_client_limit: Per-client requests per second limit
            get_client_id: Function to extract client ID from context. If None,
                           all requests are treated as the same client.
            strict_mode: If True, raise error when get_client_id returns None or fails.
                        If False, treat failed client ID resolution as 'anonymous'.
            backend: Rate limiting backend implementation. Defaults to InMemoryBackend.
            counters: Prometheus-style counters for metrics. Defaults to no counters.
            error_message_template: Custom template for error messages. Supports:
                - {retry_after}: Number of seconds to wait
                - {client_id}: Client identifier (if available)
                - {tool_name}: Tool name (if available)
                - {limit_type}: Type of limit exceeded ("global", "per_tool", "per_client")
        """
        self.global_limit = global_limit
        self.burst_capacity = burst_capacity
        self.per_tool_limit = per_tool_limit or {}
        self.per_client_limit = per_client_limit
        self.get_client_id = get_client_id
        self.strict_mode = strict_mode
        self.backend = backend or InMemoryBackend()
        self.counters = counters or DefaultRateLimitCounters()
        self.error_message_template = error_message_template

        self._lock = anyio.Lock()

        self._stats: dict[str, Any] = {
            "total_requests": 0,
            "accepted": 0,
            "rejected": 0,
            "rejected_by_rule": {
                "global": 0,
                "per_tool": 0,
                "per_client": 0,
            },
        }

        self._global_limiter: TokenBucketRateLimiter | None = None
        if global_limit is not None:
            capacity = burst_capacity or int(global_limit * 2)
            self._global_limiter = TokenBucketRateLimiter(capacity, global_limit)

        self._tool_limiters: dict[str, TokenBucketRateLimiter] = {}
        for tool_name, qps in self.per_tool_limit.items():
            capacity = burst_capacity or int(qps * 2)
            self._tool_limiters[tool_name] = TokenBucketRateLimiter(capacity, qps)

        self._client_limiters: dict[str, TokenBucketRateLimiter] = {}

    def _format_error_message(
        self,
        base_message: str,
        retry_after: float,
        client_id: str | None = None,
        tool_name: str | None = None,
        limit_type: str | None = None,
    ) -> str:
        """Format error message using the template.

        Args:
            base_message: Base error message
            retry_after: Number of seconds to wait
            client_id: Client identifier
            tool_name: Tool name
            limit_type: Type of limit exceeded

        Returns:
            Formatted error message
        """
        if self.error_message_template is None:
            return base_message

        try:
            return self.error_message_template.format(
                retry_after=retry_after,
                client_id=client_id or "unknown",
                tool_name=tool_name or "unknown",
                limit_type=limit_type or "unknown",
            )
        except (KeyError, IndexError, ValueError) as e:
            logger.warning(f"Invalid error message template: {e}")
            return base_message

    def _get_client_identifier(self, context: MiddlewareContext) -> str:
        """Get client identifier for rate limiting.

        Args:
            context: Middleware context

        Returns:
            Client identifier string

        Raises:
            ValueError: If strict_mode is True and get_client_id returns None or raises exception
        """
        if self.get_client_id is None:
            return "default"

        try:
            client_id = self.get_client_id(context)

            if client_id is None:
                if self.strict_mode:
                    raise ValueError("get_client_id returned None in strict_mode")
                logger.warning("get_client_id returned None, using 'anonymous'")
                return "anonymous"

            return str(client_id)

        except Exception as e:
            if self.strict_mode:
                raise ValueError(f"get_client_id failed: {e}") from e
            logger.warning(f"get_client_id raised exception: {e}, using 'anonymous'")
            return "anonymous"

    def _get_tool_name(self, context: MiddlewareContext) -> str | None:
        """Extract tool name from context if available.

        Args:
            context: Middleware context

        Returns:
            Tool name or None if not applicable
        """
        if context.method == "tools/call":
            message = context.message
            if hasattr(message, "name"):
                return str(message.name)
        return None

    async def _check_rate_limit(
        self,
        limiter: TokenBucketRateLimiter,
        error_message: str,
        limit_type: str,
        client_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        """Check if a rate limit is exceeded and raise appropriate error.

        Args:
            limiter: Token bucket rate limiter
            error_message: Base error message if limit is exceeded
            limit_type: Type of limit being checked ("global", "per_tool", "per_client")
            client_id: Client identifier for error message
            tool_name: Tool name for error message

        Raises:
            RateLimitExceededError: If rate limit is exceeded
        """
        allowed = await limiter.consume()
        if not allowed:
            wait_time = limiter.get_wait_time()

            formatted_message = self._format_error_message(
                base_message=error_message,
                retry_after=wait_time,
                client_id=client_id,
                tool_name=tool_name,
                limit_type=limit_type,
            )

            self._stats["rejected"] += 1
            self._stats["rejected_by_rule"][limit_type] += 1
            self.counters.increment_rejected(limit_type)

            raise RateLimitExceededError(
                message=formatted_message,
                retry_after=wait_time,
                client_id=client_id,
                tool_name=tool_name,
                limit_type=limit_type,
            )

    async def on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Apply rate limiting to requests.

        Checks rate limits in the following order:
        1. Per-tool limit (if configured for this tool)
        2. Per-client limit (if configured)
        3. Global limit (if configured)

        All limits must pass for the request to be allowed.
        """
        self._stats["total_requests"] += 1

        tool_name = self._get_tool_name(context)

        try:
            client_id = self._get_client_identifier(context)
        except ValueError as e:
            self._stats["rejected"] += 1
            self._stats["rejected_by_rule"]["per_client"] += 1
            self.counters.increment_rejected("per_client")
            raise RateLimitExceededError(
                message=f"Client ID resolution failed: {e}",
                retry_after=1.0,
                limit_type="per_client",
            ) from e

        if tool_name and tool_name in self._tool_limiters:
            await self._check_rate_limit(
                self._tool_limiters[tool_name],
                f"Rate limit exceeded for tool: {tool_name}",
                "per_tool",
                client_id=client_id,
                tool_name=tool_name,
            )

        if self.per_client_limit is not None:
            async with self._lock:
                if client_id not in self._client_limiters:
                    capacity = self.burst_capacity or int(self.per_client_limit * 2)
                    self._client_limiters[client_id] = TokenBucketRateLimiter(
                        capacity, self.per_client_limit
                    )
                client_limiter = self._client_limiters[client_id]

            await self._check_rate_limit(
                client_limiter,
                f"Rate limit exceeded for client: {client_id}",
                "per_client",
                client_id=client_id,
                tool_name=tool_name,
            )

        if self._global_limiter is not None:
            await self._check_rate_limit(
                self._global_limiter,
                "Global rate limit exceeded",
                "global",
                client_id=client_id,
                tool_name=tool_name,
            )

        self._stats["accepted"] += 1
        self.counters.increment_accepted()

        return await call_next(context)

    def update_limits(
        self,
        global_limit: float | None = None,
        per_tool_limit: dict[str, float] | None = None,
        per_client_limit: float | None = None,
        burst_capacity: int | None = None,
    ) -> None:
        """Update rate limits at runtime without restarting the server.

        Args:
            global_limit: New global QPS limit, or None to keep current
            per_tool_limit: New per-tool limits, or None to keep current
            per_client_limit: New per-client QPS limit, or None to keep current
            burst_capacity: New burst capacity, or None to keep current

        Note: Existing client limiters will be reset when per_client_limit changes.
        """
        if burst_capacity is not None:
            self.burst_capacity = burst_capacity

        if global_limit is not None:
            self.global_limit = global_limit
            capacity = self.burst_capacity or int(global_limit * 2)
            self._global_limiter = TokenBucketRateLimiter(capacity, global_limit)

        if per_tool_limit is not None:
            self.per_tool_limit = per_tool_limit
            self._tool_limiters = {}
            for tool_name, qps in per_tool_limit.items():
                capacity = self.burst_capacity or int(qps * 2)
                self._tool_limiters[tool_name] = TokenBucketRateLimiter(capacity, qps)

        if per_client_limit is not None:
            self.per_client_limit = per_client_limit
            self._client_limiters = {}

    def get_stats(self) -> RateLimitStats:
        """Get rate limiting statistics.

        Returns a dictionary containing:
        - total_requests: Total number of requests processed
        - accepted: Number of requests that passed rate limiting
        - rejected: Number of requests that were rate limited
        - rejected_by_rule: Breakdown of rejected requests by limit type
          (global, per_tool, per_client)

        Returns:
            Dictionary with rate limiting statistics
        """
        return {
            "total_requests": self._stats["total_requests"],
            "accepted": self._stats["accepted"],
            "rejected": self._stats["rejected"],
            "rejected_by_rule": {
                "global": self._stats["rejected_by_rule"]["global"],
                "per_tool": self._stats["rejected_by_rule"]["per_tool"],
                "per_client": self._stats["rejected_by_rule"]["per_client"],
            },
        }

    def reset_stats(self) -> None:
        """Reset all rate limiting statistics to zero.

        This is useful for testing or when you want to start fresh
        with a new monitoring period.
        """
        self._stats = {
            "total_requests": 0,
            "accepted": 0,
            "rejected": 0,
            "rejected_by_rule": {
                "global": 0,
                "per_tool": 0,
                "per_client": 0,
            },
        }


class SlidingWindowRateLimitingMiddleware(Middleware):
    """Middleware that implements sliding window rate limiting.

    Uses a sliding window approach which provides more precise rate limiting
    but uses more memory to track individual request timestamps.

    Example:
        ```python
        from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware

        rate_limiter = SlidingWindowRateLimitingMiddleware(
            max_requests=100,
            window_minutes=1,
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
        strict_mode: bool = False,
        counters: RateLimitCounters | None = None,
    ):
        """Initialize sliding window rate limiting middleware.

        Args:
            max_requests: Maximum requests allowed in the time window
            window_minutes: Time window in minutes
            get_client_id: Function to extract client ID from context
            strict_mode: If True, raise error when get_client_id fails
            counters: Prometheus-style counters for metrics
        """
        self.max_requests = max_requests
        self.window_seconds = window_minutes * 60
        self.get_client_id = get_client_id
        self.strict_mode = strict_mode
        self.counters = counters or DefaultRateLimitCounters()

        self._stats: dict[str, Any] = {
            "total_requests": 0,
            "accepted": 0,
            "rejected": 0,
        }

        self.limiters: dict[str, SlidingWindowRateLimiter] = defaultdict(
            lambda: SlidingWindowRateLimiter(self.max_requests, self.window_seconds)
        )

    def _get_client_identifier(self, context: MiddlewareContext) -> str:
        """Get client identifier for rate limiting.

        Args:
            context: Middleware context

        Returns:
            Client identifier string
        """
        if self.get_client_id:
            try:
                client_id = self.get_client_id(context)
                if client_id is None:
                    if self.strict_mode:
                        raise ValueError("get_client_id returned None")
                    return "anonymous"
                return str(client_id)
            except Exception as e:
                if self.strict_mode:
                    raise ValueError(f"get_client_id failed: {e}") from e
                return "anonymous"
        return "global"

    async def on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """Apply sliding window rate limiting to requests."""
        self._stats["total_requests"] += 1

        try:
            client_id = self._get_client_identifier(context)
        except ValueError as e:
            self._stats["rejected"] += 1
            self.counters.increment_rejected("per_client")
            raise RateLimitExceededError(
                message=f"Client ID resolution failed: {e}",
                retry_after=1.0,
            ) from e

        limiter = self.limiters[client_id]

        allowed = await limiter.is_allowed()
        if not allowed:
            wait_time = limiter.get_wait_time()
            self._stats["rejected"] += 1
            self.counters.increment_rejected("per_client")
            raise RateLimitExceededError(
                message=(
                    f"Rate limit exceeded: {self.max_requests} requests per "
                    f"{self.window_seconds // 60} minutes for client: {client_id}"
                ),
                retry_after=wait_time,
            )

        self._stats["accepted"] += 1
        self.counters.increment_accepted()

        return await call_next(context)

    def get_stats(self) -> dict[str, int]:
        """Get rate limiting statistics.

        Returns:
            Dictionary with total_requests, accepted, and rejected counts
        """
        return {
            "total_requests": self._stats["total_requests"],
            "accepted": self._stats["accepted"],
            "rejected": self._stats["rejected"],
        }

    def reset_stats(self) -> None:
        """Reset all rate limiting statistics to zero."""
        self._stats = {
            "total_requests": 0,
            "accepted": 0,
            "rejected": 0,
        }


def rate_limit(qps: float, burst_capacity: int | None = None):
    """Decorator to apply rate limiting to individual tool functions.

    This decorator can be used alongside the global RateLimitingMiddleware.
    Both limits will be enforced, with the stricter one taking effect first.

    Args:
        qps: Queries per second limit for this tool
        burst_capacity: Maximum burst capacity. If None, defaults to 2x qps.

    Example:
        ```python
        from fastmcp.server.middleware.rate_limiting import rate_limit

        @mcp.tool
        @rate_limit(qps=10, burst_capacity=20)
        def search(query: str) -> str:
            return f"Results for: {query}"
        ```
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        capacity = burst_capacity or int(qps * 2)
        limiter = TokenBucketRateLimiter(capacity, qps)

        if hasattr(func, "__fastmcp_tool_limiter__"):
            existing = func.__fastmcp_tool_limiter__
            new_qps = min(existing.refill_rate, qps)
            new_capacity = min(existing.capacity, capacity)
            limiter = TokenBucketRateLimiter(new_capacity, new_qps)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            allowed = await limiter.consume()
            if not allowed:
                wait_time = limiter.get_wait_time()
                raise RateLimitExceededError(
                    message=f"Rate limit exceeded for tool: {func.__name__}",
                    retry_after=wait_time,
                )
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)

        wrapper.__fastmcp_tool_limiter__ = limiter
        return wrapper

    return decorator
