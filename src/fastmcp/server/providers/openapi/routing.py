"""Deprecation shim — OpenAPI route-mapping types moved to
`fastmcp.server.plugins.openapi.routing`.
"""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.openapi.routing import (
    DEFAULT_ROUTE_MAPPINGS,
    ComponentFn,
    MCPType,
    RouteMap,
    RouteMapFn,
    _determine_route_type,
)

warnings.warn(
    "fastmcp.server.providers.openapi.routing has moved to "
    "fastmcp.server.plugins.openapi.routing. Prefer the OpenAPI plugin: "
    "`from fastmcp.server.plugins.openapi import OpenAPI`. This old "
    "leaf-submodule import path will be removed in a future release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "DEFAULT_ROUTE_MAPPINGS",
    "ComponentFn",
    "MCPType",
    "RouteMap",
    "RouteMapFn",
    "_determine_route_type",
]
