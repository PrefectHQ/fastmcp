---
title: resource
sidebarTitle: resource
---

# `fastmcp.resources.resource`


Base classes and interfaces for FastMCP resources.

## Classes

### `Resource` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L33" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Base class for all resources.


**Methods:**

#### `enable` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L51" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
enable(self) -> None
```

#### `disable` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L59" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
disable(self) -> None
```

#### `from_function` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L68" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
from_function(fn: Callable[..., Any], uri: str | AnyUrl, name: str | None = None, title: str | None = None, description: str | None = None, icons: list[Icon] | None = None, mime_type: str | None = None, tags: set[str] | None = None, enabled: bool | None = None, annotations: Annotations | None = None, meta: dict[str, Any] | None = None, task: bool | TaskConfig | None = None) -> FunctionResource
```

#### `set_default_mime_type` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L99" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
set_default_mime_type(cls, mime_type: str | None) -> str
```

Set default MIME type if not provided.


#### `set_default_name` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L106" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
set_default_name(self) -> Self
```

Set default name from URI if not provided.


#### `read` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L116" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
read(self) -> str | bytes
```

Read the resource content.

This method is not implemented in the base Resource class and must be
implemented by subclasses.


#### `to_mcp_resource` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L124" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
to_mcp_resource(self, **overrides: Any) -> MCPResource
```

Convert the resource to an MCPResource.


#### `key` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L149" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
key(self) -> str
```

The key of the component. This is used for internal bookkeeping
and may reflect e.g. prefixes or other identifiers. You should not depend on
keys having a certain value, as the same tool loaded from different
hierarchies of servers may have different keys.


### `FunctionResource` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L159" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


A resource that defers data loading by wrapping a function.

The function is only called when the resource is read, allowing for lazy loading
of potentially expensive data. This is particularly useful when listing resources,
as the function won't be called until the resource is actually accessed.

The function can return:
- str for text content (default)
- bytes for binary content
- other types will be converted to JSON


**Methods:**

#### `from_function` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L179" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
from_function(cls, fn: Callable[..., Any], uri: str | AnyUrl, name: str | None = None, title: str | None = None, description: str | None = None, icons: list[Icon] | None = None, mime_type: str | None = None, tags: set[str] | None = None, enabled: bool | None = None, annotations: Annotations | None = None, meta: dict[str, Any] | None = None, task: bool | TaskConfig | None = None) -> FunctionResource
```

Create a FunctionResource from a function.


#### `read` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/resource.py#L227" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
read(self) -> str | bytes
```

Read the resource by calling the wrapped function.

