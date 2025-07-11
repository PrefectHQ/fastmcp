# FastMCP OpenAPI Integration

This document explains how FastMCP's OpenAPI integration works, what features are supported, and how to extend it. The OpenAPI functionality is split across two main files:

- `server/openapi.py` - High-level FastMCP server implementation and MCP component creation
- `utilities/openapi.py` - Low-level OpenAPI parsing and intermediate representation

## Architecture Overview

```
OpenAPI Spec → Parse → HTTPRoute IR → Create MCP Components → FastMCP Server
```

### 1. Parsing Phase (`utilities/openapi.py`)

OpenAPI specifications are parsed into an intermediate representation (IR) that normalizes differences between OpenAPI 3.0 and 3.1:

- **Input**: Raw OpenAPI spec (dict)
- **Output**: List of `HTTPRoute` objects with normalized parameter information
- **Key Classes**: 
  - `HTTPRoute` - Represents a single operation
  - `ParameterInfo` - Represents a parameter with location, style, explode, etc.
  - `RequestBodyInfo` - Represents request body information
  - `ResponseInfo` - Represents response information

### 2. Component Creation Phase (`server/openapi.py`)

HTTPRoute objects are converted into FastMCP components based on route mapping rules:

- **Tools** (`OpenAPITool`) - HTTP operations that can be called
- **Resources** (`OpenAPIResource`) - HTTP endpoints that return data
- **Resource Templates** (`OpenAPIResourceTemplate`) - Parameterized resources

## Parameter Handling

FastMCP supports various OpenAPI parameter serialization styles and formats:

### Supported Parameter Locations
- `query` - Query string parameters
- `path` - Path parameters  
- `header` - HTTP headers
- `cookie` - Cookie parameters (parsed but not used in requests)

### Supported Parameter Styles

#### Query Parameters
- **`form`** (default) - Standard query parameter format
  - `explode=true` (default): `?tags=red&tags=blue`
  - `explode=false`: `?tags=red,blue`
- **`deepObject`** - Object parameters with bracket notation
  - `explode=true`: `?filter[name]=John&filter[age]=30`
  - `explode=false`: Falls back to JSON string (non-standard, logs warning)

#### Path Parameters  
- **`simple`** (default) - Comma-separated for arrays: `/users/1,2,3`

#### Header Parameters
- **`simple`** (default) - Standard header format

### Parameter Type Support

#### Arrays
- String arrays with `explode=true/false`
- Number arrays with `explode=true/false` 
- Boolean arrays with `explode=true/false`
- Complex object arrays (basic support, may not handle all cases)

#### Objects
- Objects with `deepObject` style and `explode=true`
- Objects with other styles fall back to JSON serialization

#### Primitives
- Strings, numbers, booleans
- Enums
- Default values

## Request Body Handling

### Supported Content Types
- `application/json` - JSON request bodies

### Schema Support
- Object schemas with properties
- Array schemas
- Primitive schemas
- Schema references (`$ref` to local schemas only)
- Required properties
- Default values

## Response Handling  

### Content Type Detection
- `application/json` - Parsed as JSON
- `text/*` - Returned as text
- `application/xml` - Returned as text
- Other types - Returned as binary

### Output Schema Generation
- Success response schemas (200, 201, 202, 204)
- Object response wrapping for MCP compliance
- Schema compression (removes unused `$defs`)

## Route Mapping

Routes are mapped to MCP component types using `RouteMap` configurations:

```python
RouteMap(
    methods=["GET", "POST"],           # HTTP methods to match
    pattern=r"/api/users/.*",          # Regex pattern for path
    mcp_type=MCPType.RESOURCE_TEMPLATE, # Target component type
    tags={"user"},                     # OpenAPI tags to match (AND condition)
    mcp_tags={"fastmcp-user"}         # Tags to add to created components
)
```

### Default Behavior
- All routes become **Tools** by default
- Use route maps to override specific patterns

### Component Types
- `MCPType.TOOL` - Callable operations
- `MCPType.RESOURCE` - Static data endpoints  
- `MCPType.RESOURCE_TEMPLATE` - Parameterized data endpoints
- `MCPType.EXCLUDE` - Skip route entirely

## Known Limitations & Edge Cases

### Parameter Edge Cases
1. **Parameter Name Collisions** - When path/query parameters have same names as request body properties, non-body parameters get `__location` suffixes
2. **Complex Array Serialization** - Limited support for arrays containing objects
3. **Cookie Parameters** - Parsed but not used in requests
4. **Non-standard Combinations** - e.g., `deepObject` with `explode=false`

### Request Body Edge Cases  
1. **Content Type Priority** - Only first available content type is used
2. **Nested Objects** - Deep nesting may not serialize correctly
3. **Binary Content** - No support for file uploads or binary data

### Response Edge Cases
1. **Multiple Content Types** - Only JSON-compatible types are used for output schemas
2. **Error Responses** - Not used for MCP output schema generation
3. **Response Headers** - Not captured or exposed

### Schema Edge Cases
1. **External References** - `$ref` to external files not supported
2. **Circular References** - May cause issues in schema processing
3. **Polymorphism** - `oneOf`/`anyOf`/`allOf` limited support

## Debugging Tips

### Common Issues
1. **"Unknown tool/resource"** - Check route mapping configuration
2. **Parameter not found** - Check for name collisions or incorrect style/explode
3. **Invalid request format** - Check parameter serialization and content types
4. **Schema validation errors** - Check for external refs or complex schemas

### Debugging Tools
```python
# Parse routes to inspect intermediate representation
routes = parse_openapi_to_http_routes(openapi_spec)
for route in routes:
    print(f"{route.method} {route.path}")
    for param in route.parameters:
        print(f"  {param.name} ({param.location}): style={param.style}, explode={param.explode}")

# Check component creation
server = FastMCP.from_openapi(openapi_spec, client)
tools = await server.get_tools()
print(f"Created {len(tools)} tools: {list(tools.keys())}")
```

### Logging
- Set `FASTMCP_LOG_LEVEL=DEBUG` to see detailed parameter processing
- Look for warnings about non-standard parameter combinations
- Check for schema parsing errors in logs

## Extension Points

### Adding New Parameter Styles
1. Add style handling in `utilities/openapi.py` - `ParameterInfo` class
2. Implement serialization logic in `server/openapi.py` - `OpenAPITool.run()`
3. Add tests for parsing and serialization

### Adding New Content Types
1. Extend request body handling in `OpenAPITool.run()`  
2. Add response parsing logic for new types
3. Update content type priority in utilities

### Custom Route Mapping
Use `route_map_fn` for complex routing logic:

```python
def custom_mapper(route: HTTPRoute, current_type: MCPType) -> MCPType:
    if route.path.startswith("/admin"):
        return MCPType.EXCLUDE
    return current_type

server = FastMCP.from_openapi(spec, client, route_map_fn=custom_mapper)
```

## Testing Patterns

### Unit Tests
- Test parameter parsing with various styles/explode combinations
- Test route mapping with different patterns and tags
- Test schema generation and compression

### Integration Tests  
- Mock HTTP client to verify actual request parameters
- Test end-to-end component creation and execution
- Test error handling and edge cases

### Example Test Pattern
```python
async def test_parameter_style():
    # 1. Create OpenAPI spec with specific parameter configuration
    spec = {"openapi": "3.1.0", ...}
    
    # 2. Parse and create components
    routes = parse_openapi_to_http_routes(spec)
    tool = OpenAPITool(mock_client, routes[0], ...)
    
    # 3. Execute and verify request parameters
    await tool.run({"param": "value"})
    actual_params = mock_client.request.call_args.kwargs["params"]
    assert actual_params == expected_params
```

## Testing

OpenAPI functionality is tested across multiple files in `tests/server/openapi/`:

- `test_basic_functionality.py` - Core component creation and execution
- `test_explode_integration.py` - Parameter explode behavior  
- `test_deepobject_style.py` - DeepObject style parameter encoding
- `test_parameter_collisions.py` - Parameter name collision handling
- `test_openapi_path_parameters.py` - Path parameter serialization
- `test_configuration.py` - Route mapping and MCP names
- `test_description_propagation.py` - Schema and description handling

When adding new OpenAPI features, create focused test files rather than adding to existing monolithic files.

---

*This document should be updated when new OpenAPI features are added or when edge cases are discovered and addressed.*