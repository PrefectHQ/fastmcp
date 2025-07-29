# OpenAPI Utilities (New Implementation)

This directory contains the next-generation OpenAPI integration utilities for FastMCP, designed to replace the legacy `openapi.py` implementation.

## Architecture Overview

The new implementation follows a **stateless request building strategy** using `openapi-core` for high-performance, per-request HTTP request construction, eliminating startup latency while maintaining robust OpenAPI compliance.

### Core Components

1. **`director.py`** - `RequestDirector` for stateless HTTP request building
2. **`parser.py`** - OpenAPI spec parsing and route extraction with pre-calculated schemas
3. **`schemas.py`** - Schema processing with parameter mapping for collision handling
4. **`models.py`** - Enhanced data models with pre-calculated fields for performance
5. **`formatters.py`** - Response formatting and processing utilities

### Key Architecture Principles

#### 1. Stateless Request Building
- Uses `openapi-core` library for robust OpenAPI parameter serialization
- Builds HTTP requests on-demand with zero startup latency
- Offloads OpenAPI compliance to a well-tested library without code generation overhead

#### 2. Pre-calculated Optimization
- **Schema Pre-calculation**: Combined schemas calculated once during parsing
- **Parameter Mapping**: Collision resolution mapping calculated upfront
- **Zero Runtime Overhead**: All complex processing done during initialization

#### 3. Performance-First Design
- **No Code Generation**: Eliminates 100-200ms startup latency
- **Serverless Friendly**: Ideal for cold-start environments
- **Minimal Dependencies**: Uses lightweight `openapi-core` instead of full client generation

## Data Flow

### Initialization Process

```
OpenAPI Spec → Parser → HTTPRoute with Pre-calculated Fields → RequestDirector + SchemaPath
```

1. **Input**: Raw OpenAPI specification (dict)
2. **Parsing**: Extract operations to `HTTPRoute` models
3. **Pre-calculation**: Generate combined schemas and parameter maps during parsing
4. **Director Setup**: Create `RequestDirector` with `SchemaPath` for request building

### Request Processing

```
MCP Tool Call → RequestDirector.build() → httpx.Request → HTTP Response → Structured Output
```

1. **Tool Invocation**: FastMCP receives tool call with parameters
2. **Request Building**: RequestDirector builds HTTP request using parameter map
3. **Parameter Handling**: openapi-core handles all OpenAPI serialization rules
4. **Response Processing**: Parse response into structured format with proper error handling

## Key Features

### 1. High-Performance Request Building
- Zero startup latency - no code generation required
- Stateless request building scales infinitely
- Uses proven `openapi-core` library for OpenAPI compliance
- Perfect for serverless and cold-start environments

### 2. Comprehensive Parameter Support
- **Parameter Collisions**: Intelligent collision resolution with suffixing
- **DeepObject Style**: Full support for deepObject parameters with explode=true/false
- **Complex Schemas**: Handles nested objects, arrays, and all OpenAPI types
- **Pre-calculated Mapping**: Parameter location mapping done upfront for performance

### 3. Enhanced Error Handling
- HTTP status code mapping to MCP errors
- Structured error responses with detailed information
- Graceful handling of network timeouts and connection errors
- Proper error context preservation

### 4. Advanced Schema Processing
- **Pre-calculated Schemas**: Combined parameter and body schemas calculated once
- **Collision-aware**: Automatically handles parameter name collisions
- **Type Safety**: Full Pydantic model validation
- **Performance**: Zero runtime schema processing overhead

## Component Integration

### Server Components (`/server/openapi_new/`)

1. **`OpenAPITool`** - Simplified tool implementation using RequestDirector
2. **`OpenAPIResource`** - Resource implementation with RequestDirector
3. **`OpenAPIResourceTemplate`** - Resource template with RequestDirector support
4. **`FastMCPOpenAPI`** - Main server class with stateless request building

### RequestDirector Integration

All components use the same RequestDirector approach:
- Consistent parameter handling across all component types
- Uniform error handling and response processing
- Simplified architecture without fallback complexity
- High performance for all operation types

## Usage Examples

### Basic Server Setup

```python
import httpx
from fastmcp.server.openapi_new import FastMCPOpenAPI

# OpenAPI spec (can be loaded from file/URL)
openapi_spec = {...}

# Create HTTP client
async with httpx.AsyncClient() as client:
    # Create server with stateless request building
    server = FastMCPOpenAPI(
        openapi_spec=openapi_spec,
        client=client,
        name="My API Server"
    )
    
    # Server automatically creates RequestDirector and pre-calculates schemas
```

### Direct RequestDirector Usage

```python
from fastmcp.experimental.utilities.openapi.director import RequestDirector
from jsonschema_path import SchemaPath

# Create RequestDirector manually
spec = SchemaPath.from_dict(openapi_spec)
director = RequestDirector(spec)

# Build HTTP request
request = director.build(route, flat_arguments, base_url)

# Execute with httpx
async with httpx.AsyncClient() as client:
    response = await client.send(request)
```

## Testing Strategy

Tests are located in `/tests/server/openapi_new/`:

### Test Categories

1. **Core Functionality**
   - `test_server.py` - Server initialization and RequestDirector integration

2. **OpenAPI Features**  
   - `test_parameter_collisions.py` - Parameter name collision handling
   - `test_deepobject_style.py` - DeepObject parameter style support
   - `test_openapi_features.py` - General OpenAPI feature compliance

### Testing Philosophy

- **Real Objects**: Use real HTTPRoute models and OpenAPI specifications
- **Minimal Mocking**: Only mock external HTTP endpoints
- **Performance Focus**: Test that initialization is fast and stateless
- **Behavioral Testing**: Verify OpenAPI compliance without implementation details

## Migration Guide

### From Legacy Implementation

1. **Import Changes**:
   ```python
   # Old
   from fastmcp.server.openapi import FastMCPOpenAPI
   
   # New  
   from fastmcp.server.openapi_new import FastMCPOpenAPI
   ```

2. **Constructor**: Same interface, no changes needed

3. **Automatic Benefits**: 
   - Eliminates startup latency (100-200ms improvement)
   - Better OpenAPI compliance via openapi-core
   - Serverless-friendly performance characteristics
   - Simplified architecture without fallback complexity

### Performance Improvements

- **Cold Start**: Zero latency penalty for serverless deployments
- **Memory Usage**: Lower memory footprint without generated client code
- **Reliability**: No dynamic code generation failures
- **Maintainability**: Simpler architecture with fewer moving parts

## Future Enhancements

### Planned Features

1. **Response Streaming**: Handle streaming API responses
2. **Enhanced Authentication**: More auth provider integrations
3. **Advanced Metrics**: Detailed request/response monitoring
4. **Schema Validation**: Enhanced input/output validation
5. **Batch Operations**: Optimized multi-operation requests

### Performance Improvements

1. **Schema Caching**: More aggressive schema pre-calculation
2. **Memory Optimization**: Further reduce memory footprint
3. **Request Batching**: Smart batching for bulk operations
4. **Connection Optimization**: Enhanced connection pooling strategies

## Troubleshooting

### Common Issues

1. **RequestDirector Initialization Fails**
   - Check OpenAPI spec validity with `jsonschema-path`
   - Verify spec format is correct JSON/YAML
   - Ensure all required OpenAPI fields are present

2. **Parameter Mapping Issues**
   - Check parameter collision resolution in debug logs
   - Verify parameter names match OpenAPI spec exactly
   - Review pre-calculated parameter map in HTTPRoute

3. **Request Building Errors**
   - Check network connectivity to target API
   - Verify base URL configuration
   - Review parameter validation and type mismatches

### Debugging

- Enable debug logging: `logger.setLevel(logging.DEBUG)`
- Check RequestDirector initialization logs
- Review parameter mapping in HTTPRoute models
- Monitor request building and API response patterns

## Dependencies

- `openapi-core` - OpenAPI specification processing and validation
- `httpx` - HTTP client library
- `pydantic` - Data validation and serialization
- `urllib.parse` - URL building and manipulation