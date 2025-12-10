---
title: dependencies
sidebarTitle: dependencies
---

# `fastmcp.server.dependencies`

## Functions

### `without_injected_parameters` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L85" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
without_injected_parameters(fn: Callable[..., Any]) -> Callable[..., Any]
```


Create a wrapper function without injected parameters.

Returns a wrapper that excludes Context and Docket dependency parameters,
making it safe to use with Pydantic TypeAdapter for schema generation and
validation. The wrapper internally handles all dependency resolution and
Context injection when called.

**Args:**
- `fn`: Original function with Context and/or dependencies

**Returns:**
- Async wrapper function without injected parameters


### `resolve_dependencies` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L208" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
resolve_dependencies(fn: Callable[..., Any], arguments: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]
```


Resolve dependencies and inject Context for a FastMCP function.

This function:
1. Filters out any dependency parameter names from user arguments (security)
2. Resolves Docket dependencies
3. Injects Context if needed
4. Merges everything together

The filtering prevents external callers from overriding injected parameters by
providing values for dependency parameter names. This is a security feature.

**Args:**
- `fn`: The function to resolve dependencies for
- `arguments`: User arguments (may contain keys that match dependency names,
      which will be filtered out)


### `get_context` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L255" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_context() -> Context
```

### `CurrentContext` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L271" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
CurrentContext() -> Context
```


Get the current FastMCP Context instance.

This dependency provides access to the active FastMCP Context for the
current MCP operation (tool/resource/prompt call).

**Returns:**
- A dependency that resolves to the active Context instance

**Raises:**
- `RuntimeError`: If no active context found (during resolution)


### `CurrentDocket` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L311" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
CurrentDocket() -> Docket
```


Get the current Docket instance managed by FastMCP.

This dependency provides access to the Docket instance that FastMCP
automatically creates for background task scheduling.

**Returns:**
- A dependency that resolves to the active Docket instance

**Raises:**
- `RuntimeError`: If not within a FastMCP server context


### `CurrentWorker` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L350" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
CurrentWorker() -> Worker
```


Get the current Docket Worker instance managed by FastMCP.

This dependency provides access to the Worker instance that FastMCP
automatically creates for background task processing.

**Returns:**
- A dependency that resolves to the active Worker instance

**Raises:**
- `RuntimeError`: If not within a FastMCP server context


### `CurrentFastMCP` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L463" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
CurrentFastMCP()
```


Get the current FastMCP server instance.

This dependency provides access to the active FastMCP server.

**Returns:**
- A dependency that resolves to the active FastMCP server

**Raises:**
- `RuntimeError`: If no server in context (during resolution)


### `get_server` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L488" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_server()
```


Get the current FastMCP server instance directly.

**Returns:**
- The active FastMCP server

**Raises:**
- `RuntimeError`: If no server in context


### `get_http_request` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L507" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_http_request() -> Request
```

### `get_http_headers` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L523" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_http_headers(include_all: bool = False) -> dict[str, str]
```


Extract headers from the current HTTP request if available.

Never raises an exception, even if there is no active HTTP request (in which case
an empty dict is returned).

By default, strips problematic headers like `content-length` that cause issues if forwarded to downstream clients.
If `include_all` is True, all headers are returned.


### `get_access_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L569" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_access_token() -> AccessToken | None
```


Get the FastMCP access token from the current context.

This function first tries to get the token from the current HTTP request's scope,
which is more reliable for long-lived connections where the SDK's auth_context_var
may become stale after token refresh. Falls back to the SDK's context var if no
request is available.

**Returns:**
- The access token if an authenticated user is available, None otherwise.


## Classes

### `InMemoryProgress` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L374" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


In-memory progress tracker for immediate tool execution.

Provides the same interface as Progress but stores state in memory
instead of Redis. Useful for testing and immediate execution where
progress doesn't need to be observable across processes.


**Methods:**

#### `current` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L392" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
current(self) -> int | None
```

#### `total` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L396" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
total(self) -> int
```

#### `message` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L400" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
message(self) -> str | None
```

#### `set_total` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L403" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
set_total(self, total: int) -> None
```

Set the total/target value for progress tracking.


#### `increment` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L409" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
increment(self, amount: int = 1) -> None
```

Atomically increment the current progress value.


#### `set_message` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L418" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
set_message(self, message: str | None) -> None
```

Update the progress status message.


### `Progress` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/dependencies.py#L423" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


FastMCP Progress dependency that works in both server and worker contexts.

Extends Docket's Progress to handle two execution modes:
- In Docket worker: Uses the execution's progress (standard Docket behavior)
- In FastMCP server: Uses in-memory progress (not observable remotely)

This allows tools to use Progress() regardless of whether they're called
immediately or as background tasks.

