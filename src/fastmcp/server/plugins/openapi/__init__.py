"""OpenAPI plugin — mount an OpenAPI spec as MCP tools/resources.

    from fastmcp import FastMCP
    from fastmcp.server.plugins.openapi import OpenAPI, OpenAPIConfig

    mcp = FastMCP(
        "Petstore",
        plugins=[OpenAPI(OpenAPIConfig(spec=petstore_spec))],
    )

Typed `RouteMap` + `MCPType` are re-exported for the Python-only
escape hatch on `OpenAPI.__init__(route_maps=...)`. Everything else
(component classes, provider class, callable type aliases) lives on the
submodules — import from `.provider`, `.components`, `.routing` directly
if you need them.
"""

from fastmcp.server.plugins.openapi.plugin import OpenAPI, OpenAPIConfig
from fastmcp.server.plugins.openapi.routing import MCPType, RouteMap

__all__ = ["MCPType", "OpenAPI", "OpenAPIConfig", "RouteMap"]
