"""Backwards-compatibility shim — OpenAPI moved to `fastmcp.server.plugins.openapi`.

The preferred entry point is now the `OpenAPI` plugin:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.openapi import OpenAPI, OpenAPIConfig

    mcp = FastMCP("Server", plugins=[OpenAPI(OpenAPIConfig(spec=...))])

`OpenAPIProvider` and its helpers (`RouteMap`, `MCPType`, component
classes) remain importable from this package for direct composition.
Importing from this top-level path does **not** emit a deprecation
warning — it stays silent so that unrelated code in fastmcp that
happens to touch `fastmcp.server.providers.openapi` doesn't spray
warnings. Users who import from the leaf submodules (`.provider`,
`.routing`, `.components`) directly will see a `FastMCPDeprecationWarning`
pointing at the new location.
"""

# Silent passthrough at the package level — re-export from the new
# location directly so neither this import nor the lazy provider import
# inside `fastmcp.server.providers.__init__` fires a deprecation warning.
from fastmcp.server.plugins.openapi.components import (
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
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
    "OpenAPIProvider",
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
    "OpenAPITool",
    "RouteMap",
    "RouteMapFn",
]
