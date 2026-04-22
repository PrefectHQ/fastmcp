"""Deprecation shim — OpenAPI route-mapping types moved to
`fastmcp.server.plugins.openapi.routing`.
"""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.openapi.routing import (
    ComponentFn,
    MCPType,
    RouteMap,
    RouteMapFn,
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
    "ComponentFn",
    "MCPType",
    "RouteMap",
    "RouteMapFn",
]
