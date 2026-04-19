"""Rate limiting backend implementations for FastMCP.

This module provides an abstraction layer for rate limiting backends,
allowing users to easily swap between in-memory and distributed
implementations (like Redis) for high-availability scenarios.

## Backend Extension Guide

To implement a custom backend (e.g., Redis, Memcached, database-backed),
create a subclass of `RateLimitBackend` and implement the required methods.

### Example: RedisBackend Skeleton

Here's a skeleton for implementing a Redis-backed rate limiter:

```python
from typing import Optional
import redis.asyncio as redis
from fastmcp.server.middleware.rate_limiting import RateLimitBackend

class RedisBackend(RateLimitBackend):
    '''Redis-based rate limiting backend for distributed scenarios.

    This backend uses Redis's atomic operations to provide thread-safe
    rate limiting across multiple server instances.

    Extension Points:
    - __init__: Configure Redis connection pool, key prefixes, TTL settings
    - try_consume: Use Redis INCR/EXPIRE or Lua script for atomic token bucket
    - get_wait_time: Calculate wait time based on remaining tokens and refill rate
    - reset: Clear rate limit state for a specific key

    Dependencies:
    - redis-py (redis.asyncio) for async Redis operations
    - Optional: redis-py-cluster for Redis Cluster support

    Example Usage:
        redis_backend = RedisBackend(
            redis_url="redis://localhost:6379/0",
            key_prefix="fastmcp:rate_limit:",
            ttl=3600,
        )
        rate_limiter = RateLimitingMiddleware(
            global_limit=100.0,
            backend=redis_backend,
        )
    '''

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "fastmcp:rate_limit:",
        ttl: int = 3600,
    ):
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.ttl = ttl
        self._redis: Optional[redis.Redis] = None

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(self.redis_url)
        return self._redis

    def _make_key(self, key: str, qps: float, burst: int) -> str:
        return f"{self.key_prefix}{key}:{qps}:{burst}"

    async def try_consume(self, key: str, qps: float, burst: int, tokens: int = 1) -> bool:
        raise NotImplementedError("RedisBackend.try_consume not implemented")

    async def get_wait_time(self, key: str, qps: float, burst: int, tokens: int = 1) -> float:
        raise NotImplementedError("RedisBackend.get_wait_time not implemented")

    async def reset(self, key: str) -> None:
        raise NotImplementedError("RedisBackend.reset not implemented")
```

### Key Design Considerations for Distributed Backends

1. **Atomicity**: Use Redis Lua scripts or pipeline with WATCH for atomic operations
2. **Serialization**: Choose between JSON, msgpack, or Redis native types
3. **TTL Management**: Set appropriate TTLs to avoid memory leaks
4. **Connection Pooling**: Configure connection pools for performance
5. **Error Handling**: Implement fallback strategies for Redis outages
6. **Monitoring**: Add metrics for Redis latency, hit rates, and errors

### Alternative Backend Ideas

- **MemcachedBackend**: Use memcached for simpler caching scenarios
- **DatabaseBackend**: Use PostgreSQL/MySQL with table-level locks
- **HybridBackend**: Combine in-memory for hot keys + Redis for cold keys
- **CircuitBreakerBackend**: Add circuit breaker pattern around any backend
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

import anyio


class RateLimitBackend(ABC):
    """Abstract base class for rate limiting backends.

    This class defines the interface for rate limiting implementations,
    allowing users to easily swap between in-memory and distributed
    implementations.

    All implementations must be thread-safe and support concurrent access.

    Example:
        # Using the default InMemoryBackend
        backend = InMemoryBackend()

        # Or using a custom backend
        backend = RedisBackend(redis_url="redis://localhost:6379/0")

        # Use with RateLimitingMiddleware
        rate_limiter = RateLimitingMiddleware(
            global_limit=100.0,
            backend=backend,
        )
    """

    @abstractmethod
    async def try_consume(
        self, key: str, qps: float, burst: int, tokens: int = 1
    ) -> bool:
        """Try to consume tokens for a given key.

        Args:
            key: Unique identifier for the rate limit (e.g., "global", "tool:search", "client:abc123")
            qps: Queries per second limit (refill rate)
            burst: Maximum burst capacity
            tokens: Number of tokens to consume (default: 1)

        Returns:
            True if tokens were consumed, False if rate limit exceeded
        """
        ...

    @abstractmethod
    async def get_wait_time(
        self, key: str, qps: float, burst: int, tokens: int = 1
    ) -> float:
        """Calculate the time needed to wait before tokens are available.

        Args:
            key: Unique identifier for the rate limit
            qps: Queries per second limit (refill rate)
            burst: Maximum burst capacity
            tokens: Number of tokens needed (default: 1)

        Returns:
            Number of seconds to wait, or 0 if tokens are available
        """
        ...

    async def reset(self, key: str) -> None:
        """Reset rate limit state for a key.

        This is optional - default implementation does nothing.
        Useful for testing or manual reset scenarios.

        Args:
            key: Unique identifier for the rate limit
        """
        pass


class InMemoryBackend(RateLimitBackend):
    """In-memory rate limiting backend using token bucket algorithm.

    This is the default backend used by RateLimitingMiddleware.
    It is suitable for single-server deployments and testing.

    Features:
    - Thread-safe using anyio.Lock
    - Token bucket algorithm with automatic refill
    - Lazy initialization of limiters
    - No external dependencies

    Example:
        from fastmcp.server.middleware.rate_limiting import InMemoryBackend

        backend = InMemoryBackend()

        # Try to consume tokens
        allowed = await backend.try_consume(
            key="global",
            qps=10.0,
            burst=20,
            tokens=1,
        )

        if not allowed:
            wait_time = await backend.get_wait_time(
                key="global",
                qps=10.0,
                burst=20,
                tokens=1,
            )
            print(f"Wait {wait_time} seconds before retrying")
    """

    def __init__(self) -> None:
        """Initialize in-memory backend."""
        self._limiters: dict[str, _TokenBucket] = {}
        self._lock = anyio.Lock()

    def _get_limiter(self, key: str, qps: float, burst: int) -> _TokenBucket:
        """Get or create a token bucket for the given key.

        Note: This method is NOT thread-safe. Callers should hold self._lock.
        """
        if key not in self._limiters:
            self._limiters[key] = _TokenBucket(capacity=burst, refill_rate=qps)
        return self._limiters[key]

    async def try_consume(
        self, key: str, qps: float, burst: int, tokens: int = 1
    ) -> bool:
        """Try to consume tokens for a given key.

        Args:
            key: Unique identifier for the rate limit
            qps: Queries per second limit (refill rate)
            burst: Maximum burst capacity
            tokens: Number of tokens to consume (default: 1)

        Returns:
            True if tokens were consumed, False if rate limit exceeded
        """
        async with self._lock:
            limiter = self._get_limiter(key, qps, burst)
            return await limiter.consume(tokens)

    async def get_wait_time(
        self, key: str, qps: float, burst: int, tokens: int = 1
    ) -> float:
        """Calculate the time needed to wait before tokens are available.

        Args:
            key: Unique identifier for the rate limit
            qps: Queries per second limit (refill rate)
            burst: Maximum burst capacity
            tokens: Number of tokens needed (default: 1)

        Returns:
            Number of seconds to wait, or 0 if tokens are available
        """
        async with self._lock:
            limiter = self._get_limiter(key, qps, burst)
            return limiter.get_wait_time(tokens)


class _TokenBucket:
    """Internal token bucket implementation for InMemoryBackend.

    This is a helper class used by InMemoryBackend. It is not meant
    to be used directly by users.

    The token bucket algorithm:
    - Tokens are added to the bucket at a fixed rate (refill_rate)
    - The bucket can hold up to capacity tokens
    - Each request consumes tokens
    - If tokens are available, the request is allowed
    - If no tokens are available, the request is rejected

    Thread Safety:
        This class is NOT thread-safe by itself. The caller (InMemoryBackend)
        is responsible for acquiring locks before accessing instances.
    """

    def __init__(self, capacity: int, refill_rate: float) -> None:
        """Initialize token bucket.

        Args:
            capacity: Maximum number of tokens the bucket can hold (burst capacity)
            refill_rate: Number of tokens added per second (sustained QPS)
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.time()

    async def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens from the bucket.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were available and consumed, False otherwise
        """
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
