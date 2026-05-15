# OpenAPI Server Implementation (New)

This directory contains the next-generation FastMCP server implementation for OpenAPI integration, designed to replace the legacy implementation in `/server/openapi.py`.

## Architecture Overview

The new implementation uses a **stateless request building approach** with `openapi-core` and `RequestDirector`, providing zero-latency startup and robust OpenAPI support optimized for serverless environments.

### Core Components

1. **`server.py`** - `FastMCPOpenAPI` main server class with RequestDirector integration
2. **`components.py`** - Simplified component implementations using RequestDirector
3. **`routing.py`** - Route mapping and component selection logic

### Key Architecture Principles

#### 1. Stateless Performance
- **Zero Startup Latency**: No code generation or heavy initialization
- **RequestDirector**: Stateless HTTP request building using openapi-core
- **Pre-calculated Schemas**: All complex processing done during parsing

#### 2. Unified Implementation
- **Single Code Path**: All components use RequestDirector consistently
- **No Fallbacks**: Simplified architecture without hybrid complexity
- **Performance First**: Optimized for cold starts and serverless deployments

#### 3. OpenAPI Compliance
- **openapi-core Integration**: Leverages proven library for parameter serialization
- **Full Feature Support**: Complete OpenAPI 3.0/3.1 support including deepObject
- **Error Handling**: Comprehensive HTTP error mapping to MCP errors

## Component Classes

### RequestDirector-Based Components

#### `OpenAPITool`
- Executes operations using RequestDirector for HTTP request building
- Automatic parameter validation and OpenAPI-compliant serialization
- Built-in error handling and structured response processing
- **Advantages**: Zero latency, robust, comprehensive OpenAPI support

#### `OpenAPIResource` / `OpenAPIResourceTemplate`  
- Provides resource access using RequestDirector
- Consistent parameter handling across all resource types
- Support for complex parameter patterns and collision resolution
- **Advantages**: High performance, simplified architecture, reliable error handling

## Server Implementation

### `FastMCPOpenAPI` Class

The main server class orchestrates the stateless request building approach:

```python
class FastMCPOpenAPI(FastMCP):
    def __init__(self, openapi_spec: dict, client: httpx.AsyncClient, **kwargs):
        # 1. Parse OpenAPI spec to HTTP routes with pre-calculated schemas
        self._routes = parse_openapi_to_http_routes(openapi_spec)
        
        # 2. Initialize RequestDirector with openapi-core Spec
        self._spec = Spec.from_dict(openapi_spec)
        self._director = RequestDirector(self._spec)
            
        # 3. Create components using RequestDirector
        self._create_components()
```

### Component Creation Logic

```python
def _create_tool(self, route: HTTPRoute) -> Tool:
    # All tools use RequestDirector for consistent, high-performance request building
    return OpenAPITool(
        client=self._client, 
        route=route, 
        director=self._director,
        name=tool_name,
        description=description,
        parameters=flat_param_schema
    )
```

## Data Flow

### Stateless Request Building

```
OpenAPI Spec → HTTPRoute with Pre-calculated Fields → RequestDirector → HTTP Request → Structured Response
```

1. **Spec Parsing**: OpenAPI spec parsed to `HTTPRoute` models with pre-calculated schemas
2. **RequestDirector Setup**: openapi-core Spec initialized for request building
3. **Component Creation**: Create components with RequestDirector reference
4. **Request Building**: RequestDirector builds HTTP request from flat parameters
5. **Request Execution**: Execute request with httpx client
6. **Response Processing**: Return structured MCP response

## Key Features

### 1. Enhanced Parameter Handling

#### Parameter Collision Resolution
- **Automatic Suffixing**: Colliding parameters get location-based suffixes
- **Example**: `id` in path and body becomes `id__path` and `id`
- **Transparent**: LLMs see suffixed parameters, implementation routes correctly

#### DeepObject Style Support
- **Native Support**: Generated client handles all deepObject variations
- **Explode Handling**: Proper support for explode=true/false
- **Complex Objects**: Nested object serialization works correctly

### 2. Robust Error Handling

#### HTTP Error Mapping
- **Status Code Mapping**: HTTP errors mapped to appropriate MCP errors
- **Structured Responses**: Error details preserved in tool results
- **Timeout Handling**: Network timeouts handled gracefully

#### Request Building Error Handling
- **Parameter Validation**: Invalid parameters caught during request building
- **Schema Validation**: openapi-core validates all OpenAPI constraints
- **Graceful Degradation**: Missing optional parameters handled smoothly

### 3. Performance Optimizations

#### Efficient Client Reuse
- **Connection Pooling**: HTTP connections reused across requests
- **Client Caching**: Generated clients cached for performance
- **Async Support**: Full async/await throughout

#### Request Optimization
- **Pre-calculated Schemas**: All complex processing done during initialization
- **Parameter Mapping**: Collision resolution handled upfront
- **Zero Latency**: No runtime code generation or complex schema processing

## Configuration

### Server Options

```python
server = FastMCPOpenAPI(
    openapi_spec=spec,           # Required: OpenAPI specification
    client=httpx_client,         # Required: HTTP client instance
    name="API Server",           # Optional: Server name
    route_map=custom_routes,     # Optional: Custom route mappings
    enable_caching=True,         # Optional: Enable response caching
)
```

### Route Mapping Customization

```python
from fastmcp.server.openapi_new.routing import RouteMap

custom_routes = RouteMap({
    "GET:/users": "tool",        # Force specific operations to be tools
    "GET:/status": "resource",   # Force specific operations to be resources
})
```

## Testing Strategy

### Test Structure

Tests are organized by functionality:
- `test_server.py` - Server integration and RequestDirector behavior
- `test_parameter_collisions.py` - Parameter collision handling
- `test_deepobject_style.py` - DeepObject parameter style support
- `test_openapi_features.py` - General OpenAPI feature compliance

### Testing Philosophy

1. **Real Integration**: Test with real OpenAPI specs and HTTP clients
2. **Minimal Mocking**: Only mock external API endpoints
3. **Behavioral Focus**: Test behavior, not implementation details
4. **Performance Focus**: Test that initialization is fast and stateless

### Example Test Pattern

```python
async def test_stateless_request_building():
    """Test that server works with stateless RequestDirector approach."""
    
    # Test server initialization is fast
    start_time = time.time()
    server = FastMCPOpenAPI(spec=valid_spec, client=client)
    init_time = time.time() - start_time
    assert init_time < 0.01  # Should be very fast
    
    # Verify RequestDirector functionality
    assert hasattr(server, '_director')
    assert hasattr(server, '_spec')
```

## Migration Benefits

### From Legacy Implementation

1. **Eliminated Startup Latency**: Zero code generation overhead (100-200ms improvement)
2. **Better OpenAPI Compliance**: openapi-core handles all OpenAPI features correctly
3. **Serverless Friendly**: Perfect for cold-start environments
4. **Simplified Architecture**: Single RequestDirector approach eliminates complexity
5. **Enhanced Reliability**: No dynamic code generation failures

### Backward Compatibility

- **Same Interface**: Public API unchanged from legacy implementation
- **Performance Improvement**: Significantly faster initialization
- **No Breaking Changes**: Existing code works without modification

## Monitoring and Debugging

### Logging

```python
# Enable debug logging to see implementation choices
import logging
logging.getLogger("fastmcp.server.openapi_new").setLevel(logging.DEBUG)
```

### Key Log Messages
- **RequestDirector Initialization**: Success/failure of RequestDirector setup
- **Schema Pre-calculation**: Pre-calculated schema and parameter map status
- **Request Building**: Parameter mapping and URL construction details
- **Performance Metrics**: Request timing and error rates

### Debugging Common Issues

1. **RequestDirector Initialization Fails**
   - Check OpenAPI spec validity with `openapi-core`
   - Verify spec format is correct JSON/YAML
   - Ensure all required OpenAPI fields are present

2. **Parameter Issues**
   - Enable debug logging for parameter processing
   - Check for parameter collision warnings
   - Verify OpenAPI spec parameter definitions

3. **Performance Issues**
   - Monitor RequestDirector request building timing
   - Check HTTP client configuration
   - Review response processing timing

## Future Enhancements

### Planned Features

1. **Advanced Caching**: Intelligent response caching with TTL
2. **Streaming Support**: Handle streaming API responses
3. **Batch Operations**: Optimize multiple operation calls
4. **Enhanced Monitoring**: Detailed metrics and health checks
5. **Configuration Management**: Dynamic configuration updates

### Performance Improvements

1. **Enhanced Schema Caching**: More aggressive schema pre-calculation
2. **Parallel Processing**: Concurrent operation execution
3. **Memory Optimization**: Further reduce memory footprint
4. **Request Optimization**: Smart request batching and deduplication

## Related Documentation

- `/utilities/openapi_new/README.md` - Utility implementation details
- `/server/openapi/README.md` - Legacy implementation reference
- `/tests/server/openapi_new/` - Comprehensive test suite
- Project documentation on OpenAPI integration patterns