---
title: config
sidebarTitle: config
---

# `fastmcp.server.tasks.config`


TaskConfig for MCP SEP-1686 background task execution modes.

This module defines the configuration for how tools, resources, and prompts
handle task-augmented execution as specified in SEP-1686.


## Classes

### `TaskConfig` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/tasks/config.py#L19" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Configuration for MCP background task execution (SEP-1686).

Controls how a component handles task-augmented requests:

- "forbidden": Component does not support task execution. Clients must not
  request task augmentation; server returns -32601 if they do.
- "optional": Component supports both synchronous and task execution.
  Client may request task augmentation or call normally.
- "required": Component requires task execution. Clients must request task
  augmentation; server returns -32601 if they don't.


**Methods:**

#### `from_bool` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/tasks/config.py#L51" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
from_bool(cls, value: bool) -> TaskConfig
```

Convert boolean task flag to TaskConfig.

**Args:**
- `value`: True for "optional" mode, False for "forbidden" mode.

**Returns:**
- TaskConfig with appropriate mode.


#### `validate_function` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/tasks/config.py#L62" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
validate_function(self, fn: Callable[..., Any], name: str) -> None
```

Validate that function is compatible with this task config.

Task execution requires async functions. Raises ValueError if mode
is "optional" or "required" but function is synchronous.

**Args:**
- `fn`: The function to validate (handles callable classes and staticmethods).
- `name`: Name for error messages.

**Raises:**
- `ValueError`: If task execution is enabled but function is sync.

