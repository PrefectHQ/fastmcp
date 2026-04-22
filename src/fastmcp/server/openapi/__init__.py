"""OpenAPI server implementation for FastMCP.

.. deprecated::
    This module is deprecated. Import from fastmcp.server.plugins.openapi instead.

The recommended approach is to use OpenAPIProvider with FastMCP:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.openapi import OpenAPIProvider
    import httpx

    client = httpx.AsyncClient(base_url="https://api.example.com")
    provider = OpenAPIProvider(openapi_spec=spec, client=client)

    mcp = FastMCP("My API Server")
    mcp.add_provider(provider)

FastMCPOpenAPI is still available but deprecated.
"""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning

warnings.warn(
    "fastmcp.server.openapi is deprecated. "
    "Import from fastmcp.server.plugins.openapi instead.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

# Re-export from new canonical location
from fastmcp.server.plugins.openapi import (  # noqa: E402
    MCPType as MCPType,
    RouteMap as RouteMap,
)
from fastmcp.server.plugins.openapi.components import (  # noqa: E402
    OpenAPIResource as OpenAPIResource,
    OpenAPIResourceTemplate as OpenAPIResourceTemplate,
    OpenAPITool as OpenAPITool,
)
from fastmcp.server.plugins.openapi.provider import (  # noqa: E402
    OpenAPIProvider as OpenAPIProvider,
)
from fastmcp.server.plugins.openapi.routing import (  # noqa: E402
    ComponentFn as ComponentFn,
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
