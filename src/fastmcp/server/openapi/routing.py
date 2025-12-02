"""Route mapping logic for OpenAPI operations."""

import enum
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .components import (
        OpenAPIResource,
        OpenAPIResourceTemplate,
        OpenAPITool,
    )
# Import from our new utilities
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.openapi import HttpMethod, HTTPRoute

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
    # PROMPT = "PROMPT"
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
# Users can provide custom route_maps to override this behavior.
DEFAULT_ROUTE_MAPPINGS = [
    RouteMap(mcp_type=MCPType.TOOL),
]


def _determine_route_type(
    route: HTTPRoute,
    mappings: list[RouteMap],
) -> RouteMap:
    """
    Determines the FastMCP component type based on the route and mappings.

    Args:
        route: HTTPRoute object
        mappings: List of RouteMap objects in priority order

    Returns:
        The RouteMap that matches the route, or a catchall "Tool" RouteMap if no match is found.
    """
    # Check mappings in priority order (first match wins)
    for route_map in mappings:
        # Check if the HTTP method matches
        if route_map.methods == "*" or route.method in route_map.methods:
            # Handle both string patterns and compiled Pattern objects
            if isinstance(route_map.pattern, Pattern):
                pattern_matches = route_map.pattern.search(route.path)
            else:
                pattern_matches = re.search(route_map.pattern, route.path)

            if pattern_matches:
                # Check if tags match (if specified)
                # If route_map.tags is empty, tags are not matched
                # If route_map.tags is non-empty, all tags must be present in route.tags (AND condition)
                if route_map.tags:
                    route_tags_set = set(route.tags or [])
                    if not route_map.tags.issubset(route_tags_set):
                        # Tags don't match, continue to next mapping
                        continue

                logger.debug(
                    f"Route {route.method} {route.path} mapped to {route_map.mcp_type.name}"
                )
                return route_map

    # Default fallback
    return RouteMap(mcp_type=MCPType.TOOL)


# Export public symbols
__all__ = [
    "DEFAULT_ROUTE_MAPPINGS",
    "ComponentFn",
    "MCPType",
    "RouteMap",
    "RouteMapFn",
    "_determine_route_type",
]
