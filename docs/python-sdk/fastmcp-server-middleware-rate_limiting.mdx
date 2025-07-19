---
title: rate_limiting
sidebarTitle: rate_limiting
---

# `fastmcp.server.middleware.rate_limiting`


Rate limiting middleware for protecting FastMCP servers from abuse.

## Classes

### `RateLimitError` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/rate_limiting.py#L15" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Error raised when rate limit is exceeded.


### `TokenBucketRateLimiter` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/rate_limiting.py#L22" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Token bucket implementation for rate limiting.


**Methods:**

#### `consume` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/rate_limiting.py#L38" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
consume(self, tokens: int = 1) -> bool
```

Try to consume tokens from the bucket.

**Args:**
- `tokens`: Number of tokens to consume

**Returns:**
- True if tokens were available and consumed, False otherwise


### `SlidingWindowRateLimiter` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/rate_limiting.py#L61" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Sliding window rate limiter implementation.


**Methods:**

#### `is_allowed` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/rate_limiting.py#L76" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
is_allowed(self) -> bool
```

Check if a request is allowed.


### `RateLimitingMiddleware` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/rate_limiting.py#L92" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Middleware that implements rate limiting to prevent server abuse.

Uses a token bucket algorithm by default, allowing for burst traffic
while maintaining a sustainable long-term rate.


**Methods:**

#### `on_request` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/rate_limiting.py#L152" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any
```

Apply rate limiting to requests.


### `SlidingWindowRateLimitingMiddleware` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/rate_limiting.py#L170" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Middleware that implements sliding window rate limiting.

Uses a sliding window approach which provides more precise rate limiting
but uses more memory to track individual request timestamps.


**Methods:**

#### `on_request` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/rate_limiting.py#L219" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any
```

Apply sliding window rate limiting to requests.

