"""Route mapping logic for OpenAPI operations."""

from __future__ import annotations

import enum
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from fastmcp.server.providers.openapi.components import (
        OpenAPIResource,
        OpenAPIResourceTemplate,
        OpenAPITool,
    )

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.openapi import HttpMethod, HTTPRoute

__all__ = [
    "ComponentFn",
    "MCPType",
    "RouteMap",
    "RouteMapFn",
]

logger = get_logger(__name__)

# Type definitions for the mapping functions
RouteMapFn = Callable[[HTTPRoute, "MCPType"], "MCPType | None"]
ComponentFn = Callable[
    [
        HTTPRoute,
        "OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate",
    ],
    None,
]


class MCPType(enum.Enum):
    """Type of FastMCP component to create from a route.

    Enum values:
        TOOL: Convert the route to a callable Tool
        RESOURCE: Convert the route to a Resource (typically GET endpoints)
        RESOURCE_TEMPLATE: Convert the route to a ResourceTemplate (typically GET with path params)
        EXCLUDE: Exclude the route from being converted to any MCP component
    """

    TOOL = "TOOL"
    RESOURCE = "RESOURCE"
    RESOURCE_TEMPLATE = "RESOURCE_TEMPLATE"
    EXCLUDE = "EXCLUDE"


@dataclass(kw_only=True)
class RouteMap:
    """Mapping configuration for HTTP routes to FastMCP component types."""

    methods: list[HttpMethod] | Literal["*"] = field(default="*")
    pattern: Pattern[str] | str = field(default=r".*")

    tags: set[str] = field(
        default_factory=set,
        metadata={"description": "A set of tags to match. All tags must match."},
    )
    mcp_type: MCPType = field(
        metadata={"description": "The type of FastMCP component to create."},
    )
    mcp_tags: set[str] = field(
        default_factory=set,
        metadata={
            "description": "A set of tags to apply to the generated FastMCP component."
        },
    )


# Default route mapping: all routes become tools.
DEFAULT_ROUTE_MAPPINGS = [
    RouteMap(mcp_type=MCPType.TOOL),
]


def _determine_route_type(
    route: HTTPRoute,
    mappings: list[RouteMap],
) -> RouteMap:
    """Determine the FastMCP component type based on the route and mappings."""
    for route_map in mappings:
        if route_map.methods == "*" or route.method in route_map.methods:
            if isinstance(route_map.pattern, Pattern):
                pattern_matches = route_map.pattern.search(route.path)
            else:
                pattern_matches = re.search(route_map.pattern, route.path)

            if pattern_matches:
                if route_map.tags:
                    route_tags_set = set(route.tags or [])
                    if not route_map.tags.issubset(route_tags_set):
                        continue

                logger.debug(
                    f"Route {route.method} {route.path} mapped to {route_map.mcp_type.name}"
                )
                return route_map

    return RouteMap(mcp_type=MCPType.TOOL)
