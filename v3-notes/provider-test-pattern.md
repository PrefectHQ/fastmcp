# Provider Tests: Direct Server Calls

This document captures the design decision to test providers via direct server method calls rather than wrapping in a Client.

## Problem

Provider tests were using the Client pattern:

```python
async with Client(mcp) as client:
    result = await client.call_tool("add", {"x": 1, "y": 2})
    assert result.data == 3
```

This conflated two concerns:
1. Does the provider/server work correctly?
2. Does the Client-Server interaction work correctly?

Additionally, ~1,200 lines of tests in `test_server_interactions.py` duplicated provider tests.

## Solution

Provider tests now call server methods directly:

```python
result = await mcp.call_tool("add", {"x": 1, "y": 2})
assert result.structured_content == {"result": 3}
```

This establishes clear test ownership:
- **Provider tests** → verify server functionality
- **Integration tests** → verify Client-Server interaction

## Result Access Patterns

Direct server calls return canonical FastMCP types, not MCP protocol types:

| Component | Access Pattern |
|-----------|----------------|
| Tool | `result.structured_content` or `result.text` |
| Resource | `result.contents[0].content` |
| Prompt | `result.messages[0].content.text` |

## Error Types

Direct calls raise FastMCP exceptions:
- `NotFoundError` - component not found
- `DisabledError` - component disabled by visibility

Client calls raise MCP protocol errors (wrapped in `McpError`).

## Implementation

- Consolidated duplicate tests from `test_server_interactions.py` into provider test files
- Reduced `test_server_interactions.py` from 1,455 → 179 lines
- Only `TestMeta` tests remain in interactions file (require Client for context injection)

## PR

- #2748 - Convert provider tests to use direct server calls
