"""OpenAPI server implementation for FastMCP - refactored for better maintainability."""

# Import from server
from .server import FastMCPOpenAPI

# Import from routing
from .routing import (
    MCPType,
    RouteMap,
    RouteMapFn,
    ComponentFn,
    DEFAULT_ROUTE_MAPPINGS,
    _determine_route_type,
)

# Import from components
from .components import (
    OpenAPITool,
    OpenAPIResource,
    OpenAPIResourceTemplate,
)

# Export public symbols - maintaining backward compatibility
__all__ = [
    "DEFAULT_ROUTE_MAPPINGS",
    "ComponentFn",
    "FastMCPOpenAPI",
    "MCPType",
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
    "OpenAPITool",
    "RouteMap",
    "RouteMapFn",
    "_determine_route_type",
]
