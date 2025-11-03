---
title: caching
sidebarTitle: caching
---

# `fastmcp.server.middleware.caching`


A middleware for response caching.

## Classes

### `CachableReadResourceContents` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L38" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


A wrapper for ReadResourceContents that can be cached.


**Methods:**

#### `get_size` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L44" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_size(self) -> int
```

#### `get_sizes` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L48" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_sizes(cls, values: Sequence[Self]) -> int
```

#### `wrap` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L52" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
wrap(cls, values: Sequence[ReadResourceContents]) -> list[Self]
```

#### `unwrap` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L56" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
unwrap(cls, values: Sequence[Self]) -> list[ReadResourceContents]
```

### `CachableToolResult` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L63" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

**Methods:**

#### `wrap` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L69" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
wrap(cls, value: ToolResult) -> Self
```

#### `unwrap` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L76" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
unwrap(self) -> ToolResult
```

### `SharedMethodSettings` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L84" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Shared config for a cache method.


### `ListToolsSettings` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L91" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Configuration options for Tool-related caching.


### `ListResourcesSettings` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L95" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Configuration options for Resource-related caching.


### `ListPromptsSettings` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L99" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Configuration options for Prompt-related caching.


### `CallToolSettings` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L103" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Configuration options for Tool-related caching.


### `ReadResourceSettings` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L110" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Configuration options for Resource-related caching.


### `GetPromptSettings` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L114" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Configuration options for Prompt-related caching.


### `ResponseCachingStatistics` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L118" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

### `ResponseCachingMiddleware` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L127" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


The response caching middleware offers a simple way to cache responses to mcp methods. The Middleware
supports cache invalidation via notifications from the server. The Middleware implements TTL-based caching
but cache implementations may offer additional features like LRU eviction, size limits, and more.

When items are retrieved from the cache they will no longer be the original objects, but rather no-op objects
this means that response caching may not be compatible with other middleware that expects original subclasses.

Notes:
- Caches `tools/call`, `resources/read`, `prompts/get`, `tools/list`, `resources/list`, and `prompts/list` requests.
- Cache keys are derived from method name and arguments.


**Methods:**

#### `on_list_tools` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L233" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
on_list_tools(self, context: MiddlewareContext[mcp.types.ListToolsRequest], call_next: CallNext[mcp.types.ListToolsRequest, Sequence[Tool]]) -> Sequence[Tool]
```

List tools from the cache, if caching is enabled, and the result is in the cache. Otherwise,
otherwise call the next middleware and store the result in the cache if caching is enabled.


#### `on_list_resources` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L273" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
on_list_resources(self, context: MiddlewareContext[mcp.types.ListResourcesRequest], call_next: CallNext[mcp.types.ListResourcesRequest, Sequence[Resource]]) -> Sequence[Resource]
```

List resources from the cache, if caching is enabled, and the result is in the cache. Otherwise,
otherwise call the next middleware and store the result in the cache if caching is enabled.


#### `on_list_prompts` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L313" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
on_list_prompts(self, context: MiddlewareContext[mcp.types.ListPromptsRequest], call_next: CallNext[mcp.types.ListPromptsRequest, Sequence[Prompt]]) -> Sequence[Prompt]
```

List prompts from the cache, if caching is enabled, and the result is in the cache. Otherwise,
otherwise call the next middleware and store the result in the cache if caching is enabled.


#### `on_call_tool` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L351" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
on_call_tool(self, context: MiddlewareContext[mcp.types.CallToolRequestParams], call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult]) -> ToolResult
```

Call a tool from the cache, if caching is enabled, and the result is in the cache. Otherwise,
otherwise call the next middleware and store the result in the cache if caching is enabled.


#### `on_read_resource` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L384" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
on_read_resource(self, context: MiddlewareContext[mcp.types.ReadResourceRequestParams], call_next: CallNext[mcp.types.ReadResourceRequestParams, Sequence[ReadResourceContents]]) -> Sequence[ReadResourceContents]
```

Read a resource from the cache, if caching is enabled, and the result is in the cache. Otherwise,
otherwise call the next middleware and store the result in the cache if caching is enabled.


#### `on_get_prompt` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L414" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
on_get_prompt(self, context: MiddlewareContext[mcp.types.GetPromptRequestParams], call_next: CallNext[mcp.types.GetPromptRequestParams, mcp.types.GetPromptResult]) -> mcp.types.GetPromptResult
```

Get a prompt from the cache, if caching is enabled, and the result is in the cache. Otherwise,
otherwise call the next middleware and store the result in the cache if caching is enabled.


#### `statistics` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/middleware/caching.py#L454" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
statistics(self) -> ResponseCachingStatistics
```

Get the statistics for the cache.

