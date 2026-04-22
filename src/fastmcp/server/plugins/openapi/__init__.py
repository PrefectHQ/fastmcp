"""OpenAPI plugin — mount an OpenAPI spec as MCP tools/resources.

The plugin is the canonical entry point:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.openapi import OpenAPI, OpenAPIConfig

    mcp = FastMCP(
        "Petstore",
        plugins=[OpenAPI(OpenAPIConfig(spec=petstore_spec))],
    )

The underlying `OpenAPIProvider` and its helper types (`RouteMap`,
`MCPType`, component classes) are re-exported here for advanced
composition. The old import path at `fastmcp.server.providers.openapi`
keeps working via deprecation shims that re-export these same symbols.
"""

from fastmcp.server.plugins.openapi.components import (
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
)
from fastmcp.server.plugins.openapi.plugin import (
    OpenAPI,
    OpenAPIConfig,
    RouteMapDict,
)
from fastmcp.server.plugins.openapi.provider import OpenAPIProvider
from fastmcp.server.plugins.openapi.routing import (
    ComponentFn,
    MCPType,
    RouteMap,
    RouteMapFn,
)

__all__ = [
    "ComponentFn",
    "MCPType",
    "OpenAPI",
    "OpenAPIConfig",
    "OpenAPIProvider",
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
    "OpenAPITool",
    "RouteMap",
    "RouteMapDict",
    "RouteMapFn",
]
