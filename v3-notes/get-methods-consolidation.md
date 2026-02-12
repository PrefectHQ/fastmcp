# Consolidating Discovery Methods

This document captures the design decisions around component listing methods in FastMCP 3.0.

## Problem

The server had parallel implementations for listing components:
- `get_tools()` / `_list_tools()`
- `get_resources()` / `_list_resources()`
- `get_prompts()` / `_list_prompts()`
- `get_resource_templates()` / `_list_resource_templates()`

These were nearly identical but with subtle differences in dedup keys, logging, and return types. The `_list_*` methods were internal and used by the MCP protocol handlers, while `get_*` methods were the public API.

## Solution

The duplicate methods were consolidated into a single set of `list_*` methods. The old `get_*` plural methods and `_list_*` internal methods were both removed.

This happened in two phases:

1. **Consolidation** (Dec 2025): Merged `get_*` and `_list_*` into a single `get_*` method with an `apply_middleware` parameter.
2. **Rename** (Jan 2026): When `FastMCP` was refactored to inherit from `Provider`, the methods were renamed to `list_*` to align with the `Provider` interface. The `apply_middleware` parameter was renamed to `run_middleware` with a default of `True`.

```python
async def list_tools(self, *, run_middleware: bool = True) -> Sequence[Tool]:
    """Canonical method for listing tools."""
    ...
```

## Key Changes

### Return Type: dict → list

The dict return type was removed because the key was redundant—components already have `.name` or `.uri` attributes.

```python
# Before (v2.x)
tools = await server.get_tools()
tool = tools["my_tool"]

# After (v3.0)
tools = await server.list_tools()
tool = next(t for t in tools if t.name == "my_tool")
```

### Middleware via Parameter

The `run_middleware=True` parameter (default) applies the middleware chain. This replaces the separate `_list_*_middleware()` methods.

## Benefits

1. **Single source of truth** - One method, not two
2. **Consistent behavior** - Same dedup key, same visibility filtering
3. **Clearer API** - Public method with explicit middleware opt-in
4. **Provider alignment** - `FastMCP.list_tools()` overrides `Provider.list_tools()`
5. **Less code** - Deleted ~200 lines of duplicate implementation

## Implementation Files

- `src/fastmcp/server/server.py` - Canonical `list_*` methods
- `src/fastmcp/server/providers/` - Provider base class defines the interface
