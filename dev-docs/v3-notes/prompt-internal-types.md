# Prompt Internal Types - Message and PromptResult

**Version:** 3.0.0
**Impact:** Breaking change for prompts returning `mcp.types.PromptMessage`

## Summary

Prompts now use FastMCP's `Message` and `PromptResult` types internally, following the same pattern as resources (#2734). MCP SDK types are only used at the protocol boundary.

## What Changed

### Before (v2.x)
```python
from mcp.types import PromptMessage, TextContent

@mcp.prompt
def my_prompt() -> PromptMessage:
    return PromptMessage(
        role="user",
        content=TextContent(type="text", text="Hello")
    )
```

### After (v3.0)
```python
from fastmcp.prompts import Message

@mcp.prompt
def my_prompt() -> Message:
    return Message("Hello")  # role defaults to "user"
```

## Type Constraints

### Prompt Function Return Types
```python
str | list[Message | str] | PromptResult
```

**Valid:**
- `return "Hello"` → wrapped as single user Message
- `return [Message("Hi"), Message("Response", role="assistant")]`
- `return ["Hi", "Response"]` → strings auto-wrapped as user Messages
- `return PromptResult(messages=[...], meta={...})`

**Invalid (now raises error):**
- `return PromptMessage(...)` → Use `Message` instead
- `return Message(...)` as single value → Use `PromptResult([Message(...)])` or return a list

### Message Class
```python
Message(
    content: Any,  # Auto-serializes non-str to JSON
    role: Literal["user", "assistant"] = "user"
)
```

**Auto-Serialization:**
- `str` → passes through as TextContent
- `dict` → JSON-serialized to text
- `list` → JSON-serialized to text
- `BaseModel` → JSON-serialized to text
- `TextContent` / `EmbeddedResource` → passes through directly

### PromptResult Class
```python
PromptResult(
    messages: str | list[Message],  # str wrapped as single Message
    description: str | None = None,
    meta: dict[str, Any] | None = None
)
```

## Why This Change?

1. **Simpler API** - `Message("Hello")` vs `PromptMessage(role="user", content=TextContent(type="text", text="Hello"))`
2. **Auto-serialization** - Dicts/lists/models automatically become JSON
3. **Consistent with resources** - Same pattern as `ResourceContent`/`ResourceResult`
4. **Type safety** - Strict typing catches errors at development time

## Migration Guide

### Simple Message
```python
# Before
from mcp.types import PromptMessage, TextContent
return PromptMessage(role="user", content=TextContent(type="text", text="Hello"))

# After
from fastmcp.prompts import Message
return Message("Hello")
```

### Conversation
```python
# Before
return [
    PromptMessage(role="user", content=TextContent(type="text", text="Hi")),
    PromptMessage(role="assistant", content=TextContent(type="text", text="Hello!")),
]

# After
return [
    Message("Hi"),
    Message("Hello!", role="assistant"),
]
```

### With Metadata
```python
from fastmcp.prompts import Message, PromptResult

return PromptResult(
    messages=[Message("Analyze this")],
    meta={"priority": "high"}
)
```

## PR

- #2738 - Introduce Message and PromptResult as canonical prompt types
