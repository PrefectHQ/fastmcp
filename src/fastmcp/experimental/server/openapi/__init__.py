"""OpenAPI server implementation for FastMCP - refactored for better maintainability."""

# Import from server
from .server import FastMCPOpenAPI

# Import from routing
from .routing import (
    MCPType,
    RouteType,  # Deprecated but kept for backward compatibility
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
    # Server
    "FastMCPOpenAPI",
    # Routing
    "MCPType",
    "RouteType",  # Deprecated but kept for backward compatibility
    "RouteMap",
    "RouteMapFn",
    "ComponentFn",
    "DEFAULT_ROUTE_MAPPINGS",
    "_determine_route_type",
    # Components
    "OpenAPITool",
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
]
