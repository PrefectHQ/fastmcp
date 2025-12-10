---
title: types
sidebarTitle: types
---

# `fastmcp.utilities.types`


Common types used across FastMCP.

## Functions

### `get_fn_name` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L34" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_fn_name(fn: Callable[..., Any]) -> str
```

### `get_cached_typeadapter` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L45" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_cached_typeadapter(cls: T) -> TypeAdapter[T]
```


TypeAdapters are heavy objects, and in an application context we'd typically
create them once in a global scope and reuse them as often as possible.
However, this isn't feasible for user-generated functions. Instead, we use a
cache to minimize the cost of creating them as much as possible.


### `issubclass_safe` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L120" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
issubclass_safe(cls: type, base: type) -> bool
```


Check if cls is a subclass of base, even if cls is a type variable.


### `is_class_member_of_type` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L130" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
is_class_member_of_type(cls: Any, base: type) -> bool
```


Check if cls is a member of base, even if cls is a type variable.

Base can be a type, a UnionType, or an Annotated type. Generic types are not
considered members (e.g. T is not a member of list\[T]).


### `find_kwarg_by_type` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L152" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
find_kwarg_by_type(fn: Callable, kwarg_type: type) -> str | None
```


Find the name of the kwarg that is of type kwarg_type.

Includes union types that contain the kwarg_type, as well as Annotated types.


### `create_function_without_params` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L178" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
create_function_without_params(fn: Callable[..., Any], exclude_params: list[str]) -> Callable[..., Any]
```


Create a new function with the same code but without the specified parameters in annotations.

This is used to exclude parameters from type adapter processing when they can't be serialized.
The excluded parameters are removed from the function's __annotations__ dictionary.


### `replace_type` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L451" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
replace_type(type_, type_map: dict[type, type])
```


Given a (possibly generic, nested, or otherwise complex) type, replaces all
instances of old_type with new_type.

This is useful for transforming types when creating tools.

**Args:**
- `type_`: The type to replace instances of old_type with new_type.
- `old_type`: The type to replace.
- `new_type`: The type to replace old_type with.

Examples:
```python
>>> replace_type(list[int | bool], {int: str})
list[str | bool]

>>> replace_type(list[list[int]], {int: str})
list[list[str]]
```


## Classes

### `FastMCPBaseModel` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L38" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Base model for FastMCP models.


### `Image` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L235" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Helper class for returning images from tools.


**Methods:**

#### `to_image_content` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L286" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
to_image_content(self, mime_type: str | None = None, annotations: Annotations | None = None) -> mcp.types.ImageContent
```

Convert to MCP ImageContent.


#### `to_data_uri` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L301" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
to_data_uri(self, mime_type: str | None = None) -> str
```

Get image as a data URI.


### `Audio` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L307" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Helper class for returning audio from tools.


**Methods:**

#### `to_audio_content` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L344" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
to_audio_content(self, mime_type: str | None = None, annotations: Annotations | None = None) -> mcp.types.AudioContent
```

### `File` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L365" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Helper class for returning file data from tools.


**Methods:**

#### `to_resource_content` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L404" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
to_resource_content(self, mime_type: str | None = None, annotations: Annotations | None = None) -> mcp.types.EmbeddedResource
```

### `ContextSamplingFallbackProtocol` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/utilities/types.py#L488" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>
