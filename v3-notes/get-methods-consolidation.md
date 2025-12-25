# Consolidating get_* and _list_* Methods

This document captures the design decision to consolidate component listing methods in FastMCP 3.0.

## Problem

The server had parallel implementations for listing components:
- `get_tools()` / `_list_tools()`
- `get_resources()` / `_list_resources()`
- `get_prompts()` / `_list_prompts()`
- `get_resource_templates()` / `_list_resource_templates()`

These were nearly identical but with subtle differences in dedup keys, logging, and return types. The `_list_*` methods were internal and used by the MCP protocol handlers, while `get_*` methods were the public API.

## Solution

`get_*` is now the canonical method. The `_list_*` methods were deleted entirely.

```python
async def get_tools(self, *, apply_middleware: bool = False) -> list[Tool]:
    """Canonical method for listing tools."""
    if apply_middleware:
        # Apply middleware chain (for MCP protocol handlers)
        mw_context = MiddlewareContext(...)
        return await self._apply_middleware(
            context=mw_context,
            call_next=lambda context: self.get_tools(apply_middleware=False)
        )

    # Core implementation: query providers, dedupe, filter visibility
    ...
```

## Key Changes

### Return Type: dict → list

The dict return type was removed because the key was redundant—components already have `.name` or `.uri` attributes.

```python
# Before
tools = await server.get_tools()
tool = tools["my_tool"]

# After
tools = await server.get_tools()
tool = next(t for t in tools if t.name == "my_tool")
```

### Middleware via Parameter

The `apply_middleware=True` parameter applies the middleware chain. This replaces the separate `_list_*_middleware()` methods.

Callers:
- MCP protocol handlers: `get_tools(apply_middleware=True)`
- Direct access: `get_tools()` (default False)

## Benefits

1. **Single source of truth** - One method, not two
2. **Consistent behavior** - Same dedup key, same visibility filtering
3. **Clearer API** - Public method with explicit middleware opt-in
4. **Less code** - Deleted ~200 lines of duplicate implementation

## Implementation Files

- `src/fastmcp/server/server.py` - Canonical `get_*` methods
- `src/fastmcp/server/providers/fastmcp_provider.py` - Uses `apply_middleware=True`
- `src/fastmcp/utilities/inspect.py` - Uses `apply_middleware=True`
