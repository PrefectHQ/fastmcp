"""Deprecated: Import from fastmcp.server.providers.openapi instead."""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning

# Deprecated in 2.14 when OpenAPI support was promoted out of experimental
warnings.warn(
    "Importing from fastmcp.experimental.server.openapi is deprecated. "
    "Import from fastmcp.server.providers.openapi instead.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

# Import from canonical location
from fastmcp.server.openapi.server import FastMCPOpenAPI as FastMCPOpenAPI  # noqa: E402
from fastmcp.server.providers.openapi import (  # noqa: E402
    ComponentFn as ComponentFn,
    MCPType as MCPType,
    OpenAPIResource as OpenAPIResource,
    OpenAPIResourceTemplate as OpenAPIResourceTemplate,
    OpenAPITool as OpenAPITool,
    RouteMap as RouteMap,
    RouteMapFn as RouteMapFn,
)
from fastmcp.server.providers.openapi.routing import (  # noqa: E402
    DEFAULT_ROUTE_MAPPINGS as DEFAULT_ROUTE_MAPPINGS,
    _determine_route_type as _determine_route_type,
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
