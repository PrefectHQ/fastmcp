"""Deprecated: Import from fastmcp.server.openapi instead."""

import warnings

from fastmcp.server.openapi import (
    ComponentFn,
    DEFAULT_ROUTE_MAPPINGS,
    FastMCPOpenAPI,
    MCPType,
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
    RouteMap,
    RouteMapFn,
    _determine_route_type,
)

# Deprecated in 2.14 when OpenAPI support was promoted out of experimental
warnings.warn(
    "Importing from fastmcp.experimental.server.openapi is deprecated. "
    "Import from fastmcp.server.openapi instead.",
    DeprecationWarning,
    stacklevel=2,
)

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
