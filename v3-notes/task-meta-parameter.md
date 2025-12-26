# Explicit task_meta Parameter for Background Tasks

This document captures the design decision to add explicit `task_meta` parameters to component execution methods, replacing context variable-based task routing.

## Problem

Background task execution used context variables (`_task_metadata`, `_docket_fn_key`) to pass task metadata through the call stack. This was implicit and had several issues:

1. **Hidden state** - Task metadata flowed through context vars, making it hard to trace
2. **Fragile enrichment** - `fn_key` was enriched in 9 different places (component methods + provider wrappers)
3. **Testing difficulty** - Required setting context vars to test background behavior
4. **No programmatic API** - Users couldn't explicitly request background execution via `call_tool()`

## Solution

Add explicit `task_meta: TaskMeta | None` parameters to:
- `FastMCP.call_tool()`, `FastMCP.read_resource()`, `FastMCP.render_prompt()`
- Component methods: `Tool._run()`, `Resource._read()`, `Prompt._render()`, `ResourceTemplate._read()`

```python
from fastmcp.server.tasks import TaskMeta

# Explicit background execution
result = await server.call_tool("my_tool", {"arg": "value"}, task_meta=TaskMeta(ttl=300))

# Returns CreateTaskResult for background, ToolResult for sync
```

## fn_key Enrichment Centralization

Previously, `fn_key` (the Docket registry key) was set in 9 places:

**Component methods (5):**
- `Tool._run()`
- `Resource._read()`
- `ResourceTemplate._read()` (2 places)
- `Prompt._render()`

**Provider wrappers (4):**
- `FastMCPProviderTool._run()`
- `FastMCPProviderResource._read()`
- `FastMCPProviderPrompt._render()`
- `FastMCPProviderResourceTemplate._read()`

Now, `fn_key` is set in **3 places** (server methods only):

```python
# In call_tool(), after finding the tool:
if task_meta is not None and task_meta.fn_key is None:
    task_meta = replace(task_meta, fn_key=tool.key)

# In read_resource(), after finding resource or template:
if task_meta is not None and task_meta.fn_key is None:
    task_meta = replace(task_meta, fn_key=resource.key)  # or template.key

# In render_prompt(), after finding the prompt:
if task_meta is not None and task_meta.fn_key is None:
    task_meta = replace(task_meta, fn_key=prompt.key)
```

## Why This Works for Mounted Servers

For mounted servers, `provider.get_tool(name)` returns a `FastMCPProviderTool` whose `.key` is already namespaced (e.g., `"tool:child_multiply"`). So setting `fn_key = tool.key` in the parent server gives the correct namespaced key.

When the provider wrapper delegates to the child server, `fn_key` is already set, so the child server won't override it.

## Type-Safe Overloads

Each method uses `@overload` to provide correct return types:

```python
@overload
async def call_tool(
    self, name: str, arguments: dict[str, Any], *, task_meta: None = None
) -> ToolResult: ...

@overload
async def call_tool(
    self, name: str, arguments: dict[str, Any], *, task_meta: TaskMeta
) -> ToolResult | mcp.types.CreateTaskResult: ...
```

## Middleware Runs Before Docket

A key fix from #2663: background tasks now properly pass through all middleware stacks before being submitted to Docket. Previously, background task submission bypassed middleware entirely.

The flow is now:
1. MCP handler extracts task metadata from request
2. Server method (`call_tool`, etc.) finds component via provider
3. Server enriches `task_meta.fn_key` with component key
4. Component's `_run()`/`_read()`/`_render()` is called
5. Middleware runs (logging, auth, rate limiting, etc.)
6. `check_background_task()` submits to Docket if task_meta present

For mounted servers, the wrapper components delegate to the child server, which runs the child's middleware before the actual execution or Docket submission.

## Removed Dead Code

- `_task_metadata` context variable
- `_docket_fn_key` context variable
- `get_task_metadata()` function
- `key` parameter in `check_background_task()` (backwards compat fallback)

## Implementation PRs

- #2663 - Components own execution; middleware runs before Docket
- #2749 - `task_meta` for `call_tool()`
- #2750 - `task_meta` for `read_resource()`
- #2751 - `task_meta` for `render_prompt()` + fn_key centralization
