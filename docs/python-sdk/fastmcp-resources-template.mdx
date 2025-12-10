---
title: template
sidebarTitle: template
---

# `fastmcp.resources.template`


Resource template functionality.

## Functions

### `extract_query_params` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L27" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
extract_query_params(uri_template: str) -> set[str]
```


Extract query parameter names from RFC 6570 `{?param1,param2}` syntax.


### `build_regex` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L35" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
build_regex(template: str) -> re.Pattern
```


Build regex pattern for URI template, handling RFC 6570 syntax.

Supports:
- `{var}` - simple path parameter
- `{var*}` - wildcard path parameter (captures multiple segments)
- `{?var1,var2}` - query parameters (ignored in path matching)


### `match_uri_template` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L61" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
match_uri_template(uri: str, uri_template: str) -> dict[str, str] | None
```


Match URI against template and extract both path and query parameters.

Supports RFC 6570 URI templates:
- Path params: `{var}`, `{var*}`
- Query params: `{?var1,var2}`


## Classes

### `ResourceTemplate` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L92" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


A template for dynamically creating resources.


**Methods:**

#### `enable` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L111" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
enable(self) -> None
```

#### `disable` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L119" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
disable(self) -> None
```

#### `from_function` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L128" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
from_function(fn: Callable[..., Any], uri_template: str, name: str | None = None, title: str | None = None, description: str | None = None, icons: list[Icon] | None = None, mime_type: str | None = None, tags: set[str] | None = None, enabled: bool | None = None, annotations: Annotations | None = None, meta: dict[str, Any] | None = None, task: bool | TaskConfig | None = None) -> FunctionResourceTemplate
```

#### `set_default_mime_type` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L159" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
set_default_mime_type(cls, mime_type: str | None) -> str
```

Set default MIME type if not provided.


#### `matches` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L165" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
matches(self, uri: str) -> dict[str, Any] | None
```

Check if URI matches template and extract parameters.


#### `read` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L169" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
read(self, arguments: dict[str, Any]) -> str | bytes
```

Read the resource content.


#### `create_resource` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L175" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
create_resource(self, uri: str, params: dict[str, Any]) -> Resource
```

Create a resource from the template with the given parameters.

The base implementation does not support background tasks.
Use FunctionResourceTemplate for task support.


#### `to_mcp_template` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L186" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
to_mcp_template(self, **overrides: Any) -> MCPResourceTemplate
```

Convert the resource template to an MCPResourceTemplate.


#### `from_mcp_template` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L208" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
from_mcp_template(cls, mcp_template: MCPResourceTemplate) -> ResourceTemplate
```

Creates a FastMCP ResourceTemplate from a raw MCP ResourceTemplate object.


#### `key` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L221" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
key(self) -> str
```

The key of the component. This is used for internal bookkeeping
and may reflect e.g. prefixes or other identifiers. You should not depend on
keys having a certain value, as the same tool loaded from different
hierarchies of servers may have different keys.


### `FunctionResourceTemplate` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L231" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


A template for dynamically creating resources.


**Methods:**

#### `create_resource` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L240" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
create_resource(self, uri: str, params: dict[str, Any]) -> Resource
```

Create a resource from the template with the given parameters.


#### `read` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L259" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
read(self, arguments: dict[str, Any]) -> str | bytes
```

Read the resource content.


#### `from_function` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/resources/template.py#L291" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
from_function(cls, fn: Callable[..., Any], uri_template: str, name: str | None = None, title: str | None = None, description: str | None = None, icons: list[Icon] | None = None, mime_type: str | None = None, tags: set[str] | None = None, enabled: bool | None = None, annotations: Annotations | None = None, meta: dict[str, Any] | None = None, task: bool | TaskConfig | None = None) -> FunctionResourceTemplate
```

Create a template from a function.

