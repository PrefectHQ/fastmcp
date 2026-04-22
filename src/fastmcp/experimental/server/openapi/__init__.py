"""Deprecated: Import from fastmcp.server.plugins.openapi instead."""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning

# Deprecated in 2.14 when OpenAPI support was promoted out of experimental
warnings.warn(
    "Importing from fastmcp.experimental.server.openapi is deprecated. "
    "Import from fastmcp.server.plugins.openapi instead.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

# Import from canonical location
from fastmcp.server.openapi.server import FastMCPOpenAPI as FastMCPOpenAPI  # noqa: E402
from fastmcp.server.plugins.openapi import (  # noqa: E402
    MCPType as MCPType,
    RouteMap as RouteMap,
)
from fastmcp.server.plugins.openapi.components import (  # noqa: E402
    OpenAPIResource as OpenAPIResource,
    OpenAPIResourceTemplate as OpenAPIResourceTemplate,
    OpenAPITool as OpenAPITool,
)
from fastmcp.server.plugins.openapi.routing import (  # noqa: E402
    ComponentFn as ComponentFn,
    RouteMapFn as RouteMapFn,
)
from fastmcp.server.plugins.openapi.routing import (  # noqa: E402
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
