# Visibility & Enable/Disable Design

This document captures the design decisions for the enable/disable system in FastMCP 3.0.

## Core Principle

**Components describe capabilities. Servers and providers control availability.**

Previously, each component had an `enabled` field that users could mutate directly. This caused a fundamental problem: when components pass through providers (especially TransformingProvider), you receive copies—and mutating a copy doesn't affect the original.

## Solution: Hierarchical Visibility

Both servers and providers maintain their own `VisibilityFilter`. If a component is disabled at any level, it's disabled up the chain.

```
Provider A (filters) → Provider B (filters) → Server (filters) → Client sees only enabled components
```

## VisibilityFilter

The `VisibilityFilter` class (`src/fastmcp/utilities/visibility.py`) provides:

### Blocklist (disable)
```python
server.disable(keys=["tool:my_tool"])  # Hide specific component
server.disable(tags={"internal"})       # Hide all components with tag
```

### Allowlist (enable with only=True)
```python
server.enable(tags={"public"}, only=True)  # Show ONLY components with tag
```

### Blocklist Wins
If a component is in both blocklist and allowlist, blocklist wins. This ensures you can always hide something regardless of other filters.

### Change Detection
The `VisibilityFilter` only sends notifications when visibility actually changes:
- Disabling an already-disabled component: no notification
- Enabling an already-enabled component: no notification
- Actual state change: notification sent

## Vocabulary

Consistent verbs throughout the codebase:
- `enable()` / `disable()` - methods on servers and providers
- `is_enabled()` - check if component is visible
- `_disabled_keys`, `_disabled_tags` - blocklist state
- `_enabled_keys`, `_enabled_tags` - allowlist state
- `_default_enabled` - True unless `only=True` was used

## Notifications

`VisibilityFilter` handles notifications directly via `_send_notification()`. This:
1. Gets the current request context (if any)
2. Queues the appropriate list-changed notification
3. No-ops gracefully outside request context

This simplifies the code—no callback wiring needed between VisibilityFilter and its owners.

## Migration from 2.x

### Component enable/disable removed

```python
# Before (2.x) - BROKEN: mutates a copy
tool.disable()

# After (3.x)
server.disable(keys=["tool:my_tool"])
```

### enabled field removed

```python
# Before (2.x)
@mcp.tool(enabled=False)
def my_tool(): ...

# After (3.x)
@mcp.tool
def my_tool(): ...

mcp.disable(keys=["tool:my_tool"])
```

### include_tags/exclude_tags deprecated

```python
# Before (deprecated)
mcp = FastMCP("server", exclude_tags={"internal"})

# After
mcp = FastMCP("server")
mcp.disable(tags={"internal"})
```

## Component Keys

Components use prefixed keys for enable/disable:
- Tools: `"tool:function_name"`
- Prompts: `"prompt:prompt_name"`
- Resources: `"resource:resource://uri"`
- Templates: `"template:resource://{param}/path"`

Use `component.key` to get the correct key format.

## Implementation Files

- `src/fastmcp/utilities/visibility.py` - VisibilityFilter class
- `src/fastmcp/server/providers/base.py` - Provider.enable/disable
- `src/fastmcp/server/server.py` - FastMCP.enable/disable
- `src/fastmcp/utilities/components.py` - Component.enable/disable raise NotImplementedError
