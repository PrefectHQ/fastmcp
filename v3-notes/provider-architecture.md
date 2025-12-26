# Provider Architecture: FastMCPProvider + TransformingProvider

**Version:** 3.0.0
**Impact:** Breaking change - `MountedProvider` removed

## Summary

The monolithic `MountedProvider` was split into two focused, composable components:

- **`FastMCPProvider`**: Wraps a FastMCP server, exposing its components through the Provider interface
- **`TransformingProvider`**: Wraps any provider to apply namespace prefixes and tool renames

## Why the Split?

`MountedProvider` was doing two things:
1. Wrapping a FastMCP server as a provider
2. Transforming component names with prefixes

Separating these concerns enables:
- Reusing transformations on any provider (not just FastMCP servers)
- Stacking transformations via composition
- Clearer mental model

## New API

### FastMCPProvider

Wraps a FastMCP server to expose it through the Provider interface:

```python
from fastmcp.server.providers import FastMCPProvider

sub_server = FastMCP("Sub")

@sub_server.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"

# Wrap as provider
provider = FastMCPProvider(sub_server)
main_server.add_provider(provider)
```

### TransformingProvider

Wraps any provider to apply transformations:

```python
# Apply namespace to all components
provider = FastMCPProvider(server).with_namespace("api")
# "my_tool" → "api_my_tool"
# "resource://data" → "resource://api/data"

# Rename specific tools (bypasses namespace)
provider = FastMCPProvider(server).with_transforms(
    namespace="api",
    tool_renames={"verbose_tool_name": "short"}
)
# "verbose_tool_name" → "short"
# "other_tool" → "api_other_tool"
```

### Stacking Transformations

Transformations compose via stacking:

```python
provider = (
    FastMCPProvider(server)
    .with_namespace("inner")
    .with_namespace("outer")
)
# "tool" → "outer_inner_tool"
```

## mount() Uses This Internally

`FastMCP.mount()` now creates a `FastMCPProvider` + `TransformingProvider` internally:

```python
main.mount(sub, namespace="api")

# Equivalent to:
main.add_provider(
    FastMCPProvider(sub).with_namespace("api")
)
```

## Breaking Changes

### MountedProvider Removed

```python
# Before (2.x)
from fastmcp.server.providers import MountedProvider
provider = MountedProvider(server, prefix="api")

# After (3.x)
from fastmcp.server.providers import FastMCPProvider
provider = FastMCPProvider(server).with_namespace("api")
```

### prefix → namespace

```python
# Before (deprecated)
main.mount(sub, prefix="api")

# After
main.mount(sub, namespace="api")
```

## Implementation PRs

- #2653 - Split MountedProvider into FastMCPProvider + TransformingProvider
- #2635 - Initial MountedProvider (superseded by #2653)
