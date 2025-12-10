---
title: context
sidebarTitle: context
---

# `fastmcp.server.context`

## Functions

### `set_context` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L89" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
set_context(context: Context) -> Generator[Context, None, None]
```

## Classes

### `LogData` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L65" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Data object for passing log arguments to client-side handlers.

This provides an interface to match the Python standard library logging,
for compatibility with structured logging.


### `Context` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L98" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Context object providing access to MCP capabilities.

This provides a cleaner interface to MCP's RequestContext functionality.
It gets injected into tool and resource functions that request it via type hints.

To use context in a tool function, add a parameter with the Context type annotation:

```python
@server.tool
async def my_tool(x: int, ctx: Context) -> str:
    # Log messages to the client
    await ctx.info(f"Processing {x}")
    await ctx.debug("Debug info")
    await ctx.warning("Warning message")
    await ctx.error("Error message")

    # Report progress
    await ctx.report_progress(50, 100, "Processing")

    # Access resources
    data = await ctx.read_resource("resource://data")

    # Get request info
    request_id = ctx.request_id
    client_id = ctx.client_id

    # Manage state across the request
    ctx.set_state("key", "value")
    value = ctx.get_state("key")

    return str(x)
```

State Management:
Context objects maintain a state dictionary that can be used to store and share
data across middleware and tool calls within a request. When a new context
is created (nested contexts), it inherits a copy of its parent's state, ensuring
that modifications in child contexts don't affect parent contexts.

The context parameter name can be anything as long as it's annotated with Context.
The context is optional - tools that don't need it can omit the parameter.


**Methods:**

#### `fastmcp` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L150" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
fastmcp(self) -> FastMCP
```

Get the FastMCP instance.


#### `request_context` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L193" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
request_context(self) -> RequestContext[ServerSession, Any, Request] | None
```

Access to the underlying request context.

Returns None when the MCP session has not been established yet.
Returns the full RequestContext once the MCP session is available.

For HTTP request access in middleware, use `get_http_request()` from fastmcp.server.dependencies,
which works whether or not the MCP session is available.

Example in middleware:
```python
async def on_request(self, context, call_next):
    ctx = context.fastmcp_context
    if ctx.request_context:
        # MCP session available - can access session_id, request_id, etc.
        session_id = ctx.session_id
    else:
        # MCP session not available yet - use HTTP helpers
        from fastmcp.server.dependencies import get_http_request
        request = get_http_request()
    return await call_next(context)
```


#### `report_progress` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L221" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
report_progress(self, progress: float, total: float | None = None, message: str | None = None) -> None
```

Report progress for the current operation.

**Args:**
- `progress`: Current progress value e.g. 24
- `total`: Optional total value e.g. 100


#### `list_resources` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L248" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
list_resources(self) -> list[MCPResource]
```

List all available resources from the server.

**Returns:**
- List of Resource objects available on the server


#### `list_prompts` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L256" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
list_prompts(self) -> list[MCPPrompt]
```

List all available prompts from the server.

**Returns:**
- List of Prompt objects available on the server


#### `get_prompt` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L264" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> GetPromptResult
```

Get a prompt by name with optional arguments.

**Args:**
- `name`: The name of the prompt to get
- `arguments`: Optional arguments to pass to the prompt

**Returns:**
- The prompt result


#### `read_resource` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L278" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
read_resource(self, uri: str | AnyUrl) -> list[ReadResourceContents]
```

Read a resource by URI.

**Args:**
- `uri`: Resource URI to read

**Returns:**
- The resource content as either text or bytes


#### `log` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L290" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
log(self, message: str, level: LoggingLevel | None = None, logger_name: str | None = None, extra: Mapping[str, Any] | None = None) -> None
```

Send a log message to the client.

Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`.

**Args:**
- `message`: Log message
- `level`: Optional log level. One of "debug", "info", "notice", "warning", "error", "critical",
"alert", or "emergency". Default is "info".
- `logger_name`: Optional logger name
- `extra`: Optional mapping for additional arguments


#### `client_id` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L319" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
client_id(self) -> str | None
```

Get the client ID if available.


#### `request_id` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L328" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
request_id(self) -> str
```

Get the unique ID for this request.

Raises RuntimeError if MCP request context is not available.


#### `session_id` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L341" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
session_id(self) -> str
```

Get the MCP session ID for ALL transports.

Returns the session ID that can be used as a key for session-based
data storage (e.g., Redis) to share data between tool calls within
the same client session.

**Returns:**
- The session ID for StreamableHTTP transports, or a generated ID
- for other transports.


#### `session` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L393" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
session(self) -> ServerSession
```

Access to the underlying session for advanced usage.

Raises RuntimeError if MCP request context is not available.


#### `debug` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L406" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
debug(self, message: str, logger_name: str | None = None, extra: Mapping[str, Any] | None = None) -> None
```

Send a `DEBUG`-level message to the connected MCP Client.

Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`.


#### `info` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L422" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
info(self, message: str, logger_name: str | None = None, extra: Mapping[str, Any] | None = None) -> None
```

Send a `INFO`-level message to the connected MCP Client.

Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`.


#### `warning` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L438" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
warning(self, message: str, logger_name: str | None = None, extra: Mapping[str, Any] | None = None) -> None
```

Send a `WARNING`-level message to the connected MCP Client.

Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`.


#### `error` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L454" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
error(self, message: str, logger_name: str | None = None, extra: Mapping[str, Any] | None = None) -> None
```

Send a `ERROR`-level message to the connected MCP Client.

Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`.


#### `list_roots` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L470" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
list_roots(self) -> list[Root]
```

List the roots available to the server, as indicated by the client.


#### `send_tool_list_changed` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L475" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
send_tool_list_changed(self) -> None
```

Send a tool list changed notification to the client.


#### `send_resource_list_changed` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L479" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
send_resource_list_changed(self) -> None
```

Send a resource list changed notification to the client.


#### `send_prompt_list_changed` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L483" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
send_prompt_list_changed(self) -> None
```

Send a prompt list changed notification to the client.


#### `close_sse_stream` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L487" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
close_sse_stream(self) -> None
```

Close the current response stream to trigger client reconnection.

When using StreamableHTTP transport with an EventStore configured, this
method gracefully closes the HTTP connection for the current request.
The client will automatically reconnect (after `retry_interval` milliseconds)
and resume receiving events from where it left off via the EventStore.

This is useful for long-running operations to avoid load balancer timeouts.
Instead of holding a connection open for minutes, you can periodically close
and let the client reconnect.


#### `sample` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L526" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
sample(self, messages: str | Sequence[str | SamplingMessage], system_prompt: str | None = None, include_context: IncludeContext | None = None, temperature: float | None = None, max_tokens: int | None = None, model_preferences: ModelPreferences | str | list[str] | None = None) -> SamplingMessageContentBlock | list[SamplingMessageContentBlock]
```

Send a sampling request to the client and await the response.

Call this method at any time to have the server request an LLM
completion from the client. The client must be appropriately configured,
or the request will error.


#### `elicit` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L610" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
elicit(self, message: str, response_type: None) -> AcceptedElicitation[dict[str, Any]] | DeclinedElicitation | CancelledElicitation
```

#### `elicit` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L622" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
elicit(self, message: str, response_type: type[T]) -> AcceptedElicitation[T] | DeclinedElicitation | CancelledElicitation
```

#### `elicit` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L632" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
elicit(self, message: str, response_type: list[str]) -> AcceptedElicitation[str] | DeclinedElicitation | CancelledElicitation
```

#### `elicit` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L641" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
elicit(self, message: str, response_type: type[T] | list[str] | None = None) -> AcceptedElicitation[T] | AcceptedElicitation[dict[str, Any]] | AcceptedElicitation[str] | DeclinedElicitation | CancelledElicitation
```

Send an elicitation request to the client and await the response.

Call this method at any time to request additional information from
the user through the client. The client must support elicitation,
or the request will error.

Note that the MCP protocol only supports simple object schemas with
primitive types. You can provide a dataclass, TypedDict, or BaseModel to
comply. If you provide a primitive type, an object schema with a single
"value" field will be generated for the MCP interaction and
automatically deconstructed into the primitive type upon response.

If the response_type is None, the generated schema will be that of an
empty object in order to comply with the MCP protocol requirements.
Clients must send an empty object ("{}")in response.

**Args:**
- `message`: A human-readable message explaining what information is needed
- `response_type`: The type of the response, which should be a primitive
type or dataclass or BaseModel. If it is a primitive type, an
object schema with a single "value" field will be generated.


#### `set_state` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L692" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
set_state(self, key: str, value: Any) -> None
```

Set a value in the context state.


#### `get_state` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/context.py#L696" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_state(self, key: str) -> Any
```

Get a value from the context state. Returns None if the key is not found.

