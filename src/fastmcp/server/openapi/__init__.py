"""OpenAPI server implementation for FastMCP.

.. deprecated::
    This module is deprecated. Import from fastmcp.server.providers.openapi instead.

The recommended approach is to use OpenAPIProvider with FastMCP:

    from fastmcp import FastMCP
    from fastmcp.server.providers.openapi import OpenAPIProvider
    import httpx

    client = httpx.AsyncClient(base_url="https://api.example.com")
    provider = OpenAPIProvider(openapi_spec=spec, client=client)

    mcp = FastMCP("My API Server")
    mcp.add_provider(provider)

FastMCPOpenAPI is still available but deprecated.
"""

import warnings

warnings.warn(
    "fastmcp.server.openapi is deprecated. "
    "Import from fastmcp.server.providers.openapi instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new canonical location
from fastmcp.server.providers.openapi import (  # noqa: E402
    ComponentFn as ComponentFn,
    MCPType as MCPType,
    OpenAPIProvider as OpenAPIProvider,
    OpenAPIResource as OpenAPIResource,
    OpenAPIResourceTemplate as OpenAPIResourceTemplate,
    OpenAPITool as OpenAPITool,
    RouteMap as RouteMap,
    RouteMapFn as RouteMapFn,
)

# Keep FastMCPOpenAPI for backwards compat (it has its own deprecation warning)
from fastmcp.server.openapi.server import FastMCPOpenAPI as FastMCPOpenAPI  # noqa: E402

__all__ = [
    "ComponentFn",
    "FastMCPOpenAPI",
    "MCPType",
    "OpenAPIProvider",
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
    "OpenAPITool",
    "RouteMap",
    "RouteMapFn",
]
