"""Route mapping logic for OpenAPI operations.

.. deprecated::
    This module is deprecated. Import from fastmcp.server.providers.openapi instead.
"""

# ruff: noqa: E402

import warnings

# Backwards compatibility - export everything that was previously public
__all__ = [
    "DEFAULT_ROUTE_MAPPINGS",
    "ComponentFn",
    "MCPType",
    "RouteMap",
    "RouteMapFn",
    "_determine_route_type",
]

warnings.warn(
    "fastmcp.server.openapi.routing is deprecated. "
    "Import from fastmcp.server.providers.openapi instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new canonical location
from fastmcp.server.providers.openapi.routing import (
    DEFAULT_ROUTE_MAPPINGS as DEFAULT_ROUTE_MAPPINGS,
)
from fastmcp.server.providers.openapi.routing import (
    ComponentFn as ComponentFn,
)
from fastmcp.server.providers.openapi.routing import (
    MCPType as MCPType,
)
from fastmcp.server.providers.openapi.routing import (
    RouteMap as RouteMap,
)
from fastmcp.server.providers.openapi.routing import (
    RouteMapFn as RouteMapFn,
)
from fastmcp.server.providers.openapi.routing import (
    _determine_route_type as _determine_route_type,
)
