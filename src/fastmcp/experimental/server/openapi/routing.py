"""Route mapping logic for OpenAPI operations."""

import enum
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern
from typing import TYPE_CHECKING, Literal

import fastmcp

if TYPE_CHECKING:
    from .components import (
        OpenAPIResource,
        OpenAPIResourceTemplate,
        OpenAPITool,
    )
# Import from our new utilities
from fastmcp.experimental.utilities.openapi import HttpMethod, HTTPRoute
from fastmcp.utilities.logging import get_logger

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
        IGNORE: Deprecated, use EXCLUDE instead
    """

    TOOL = "TOOL"
    RESOURCE = "RESOURCE"
    RESOURCE_TEMPLATE = "RESOURCE_TEMPLATE"
    # PROMPT = "PROMPT"
    EXCLUDE = "EXCLUDE"


# Keep RouteType as an alias to MCPType for backward compatibility
class RouteType(enum.Enum):
    """
    Deprecated: Use MCPType instead.

    This enum is kept for backward compatibility and will be removed in a future version.
    """

    TOOL = "TOOL"
    RESOURCE = "RESOURCE"
    RESOURCE_TEMPLATE = "RESOURCE_TEMPLATE"
    IGNORE = "IGNORE"


@dataclass(kw_only=True)
class RouteMap:
    """Mapping configuration for HTTP routes to FastMCP component types."""

    methods: list[HttpMethod] | Literal["*"] = field(default="*")
    pattern: Pattern[str] | str = field(default=r".*")
    route_type: RouteType | MCPType | None = field(default=None)
    tags: set[str] = field(
        default_factory=set,
        metadata={"description": "A set of tags to match. All tags must match."},
    )
    mcp_type: MCPType | None = field(
        default=None,
        metadata={"description": "The type of FastMCP component to create."},
    )
    mcp_tags: set[str] = field(
        default_factory=set,
        metadata={
            "description": "A set of tags to apply to the generated FastMCP component."
        },
    )

    def __post_init__(self):
        """Validate and process the route map after initialization."""
        # Handle backward compatibility for route_type, deprecated in 2.5.0
        if self.mcp_type is None and self.route_type is not None:
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "The 'route_type' parameter is deprecated and will be removed in a future version. "
                    "Use 'mcp_type' instead with the appropriate MCPType value.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            if isinstance(self.route_type, RouteType):
                if fastmcp.settings.deprecation_warnings:
                    warnings.warn(
                        "The RouteType class is deprecated and will be removed in a future version. "
                        "Use MCPType instead.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
            # Check for the deprecated IGNORE value
            if self.route_type == RouteType.IGNORE:
                if fastmcp.settings.deprecation_warnings:
                    warnings.warn(
                        "RouteType.IGNORE is deprecated and will be removed in a future version. "
                        "Use MCPType.EXCLUDE instead.",
                        DeprecationWarning,
                        stacklevel=2,
                    )

            # Convert from RouteType to MCPType if needed
            if isinstance(self.route_type, RouteType):
                route_type_name = self.route_type.name
                if route_type_name == "IGNORE":
                    route_type_name = "EXCLUDE"
                self.mcp_type = getattr(MCPType, route_type_name)
            else:
                self.mcp_type = self.route_type
        elif self.mcp_type is None:
            raise ValueError("`mcp_type` must be provided")

        # Set route_type to match mcp_type for backward compatibility
        if self.route_type is None:
            self.route_type = self.mcp_type


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

                # We know mcp_type is not None here due to post_init validation
                assert route_map.mcp_type is not None
                logger.debug(
                    f"Route {route.method} {route.path} matched mapping to {route_map.mcp_type.name}"
                )
                return route_map

    # Default fallback
    return RouteMap(mcp_type=MCPType.TOOL)


# Export public symbols
__all__ = [
    "MCPType",
    "RouteType",  # Deprecated but kept for backward compatibility
    "RouteMap",
    "RouteMapFn",
    "ComponentFn",
    "DEFAULT_ROUTE_MAPPINGS",
    "_determine_route_type",
]
