# Resource Internal Types - Strict Typing for Type Safety

**Version:** 3.0.0
**Impact:** Breaking change for resources returning dict/list

## Summary

ResourceResult now enforces strict typing to catch errors at development time (via type checker) rather than at runtime (when a client reads a resource).

## What Changed

### Before (v2.x)
```python
@mcp.resource("data://config")
def get_config() -> dict:  # Auto-serialized to JSON
    return {"key": "value"}

@mcp.resource("data://items")
def get_items() -> list:  # Each item auto-wrapped
    return ["item1", "item2"]

ResourceResult({"key": "value"})  # Dict auto-converted
ResourceResult(["a", "b"])         # List split into items
```

### After (v3.0)
```python
@mcp.resource("data://config")
def get_config() -> str:  # Explicit JSON serialization
    import json
    return json.dumps({"key": "value"})

@mcp.resource("data://items")
def get_items() -> ResourceResult:  # Explicit multi-item response
    return ResourceResult([
        ResourceContent("item1"),
        ResourceContent("item2"),
    ])

ResourceResult([ResourceContent(...)])  # Explicit list wrapping
# Dict/list raises TypeError
```

## Type Constraints

### Resource.read() Return Type
```python
str | bytes | ResourceResult
```

**Valid:**
- `return "text content"`
- `return b"binary data"`
- `return ResourceResult([ResourceContent(...)])`

**Invalid (now raises TypeError):**
- `return {"key": "value"}` → Use `json.dumps()` instead
- `return ["item1", "item2"]` → Use `ResourceResult([ResourceContent(...)])`
- `return ResourceContent(...)` → Use `ResourceResult([ResourceContent(...)])`

### ResourceResult Type Signature
```python
ResourceResult(
    contents: str | bytes | list[ResourceContent],
    meta: dict[str, Any] | None = None
)
```

**Valid:**
- `ResourceResult("plain text")`
- `ResourceResult(b"binary")`
- `ResourceResult([ResourceContent(...), ResourceContent(...)])`

**Invalid (now raises TypeError):**
- `ResourceResult({"key": "value"})` → Dict not supported
- `ResourceResult(["a", "b"])` → Bare list not supported (must be list[ResourceContent])
- `ResourceResult(resource_content_obj)` → Single item must be in list

### ResourceContent Type Signature
```python
ResourceContent(
    content: Any,  # Auto-serializes non-str/bytes to JSON
    mime_type: str | None = None,
    meta: dict[str, Any] | None = None
)
```

**Auto-Serialization in ResourceContent.__init__:**
- `str` → passes through (mime_type defaults to "text/plain")
- `bytes` → passes through (mime_type defaults to "application/octet-stream")
- `dict` → JSON-serialized string (mime_type defaults to "application/json")
- `list` → JSON-serialized string (mime_type defaults to "application/json")
- `BaseModel` → JSON-serialized string (mime_type defaults to "application/json")

## Why This Change?

The old auto-conversion behavior was convenient but hid errors:

```python
# Old behavior - silent failure
return ["item1", "item2"]  # Client sees 2 items OR JSON array?
# Ambiguous! Users would discover issues only when client reads resource

# New behavior - caught at dev time
return ["item1", "item2"]  # Type checker error immediately
# Must explicitly write:
return json.dumps(["item1", "item2"])  # Clear intent
# OR:
return ResourceResult([ResourceContent("item1"), ResourceContent("item2")])
```

Type checkers now catch return type mismatches during development rather than at runtime.

## Migration Guide

### Returning JSON Data
**Before:**
```python
def get_config() -> dict:
    return {"key": "value", "nested": {"a": 1}}
```

**After:**
```python
import json

def get_config() -> str:
    return json.dumps({"key": "value", "nested": {"a": 1}})
```

### Returning Multiple Items
**Before:**
```python
def get_items() -> list:
    return ["user1", "user2", "user3"]
```

**After (Option 1: Single JSON array):**
```python
import json

def get_items() -> str:
    return json.dumps(["user1", "user2", "user3"])
```

**After (Option 2: Multiple content items):**
```python
from fastmcp.resources import ResourceContent, ResourceResult

def get_items() -> ResourceResult:
    return ResourceResult([
        ResourceContent("user1"),
        ResourceContent("user2"),
        ResourceContent("user3"),
    ])
```

### Returning Structured Data with Custom MIME Types
**Before:**
```python
def get_html() -> dict:
    return {"html": "<div>content</div>"}
```

**After:**
```python
from fastmcp.resources import ResourceContent, ResourceResult

def get_html() -> ResourceResult:
    return ResourceResult([
        ResourceContent(
            content="<div>content</div>",
            mime_type="text/html"
        )
    ])
```

## Type Checking

Your type checker will now catch these errors:

```python
@mcp.resource("data://test")
def bad_resource() -> dict:  # ← Type error: should be str | bytes | ResourceResult
    return {"key": "value"}
```

This is intentional. The type system enforces correct typing at development time.

## Backward Compatibility

**This is a breaking change.** Code that returns dict or list from resources will:
1. **Pass type checking**: If you ignore type warnings
2. **Fail at runtime**: Raises `TypeError` when client reads the resource

Migrate to explicit JSON serialization or ResourceResult.
