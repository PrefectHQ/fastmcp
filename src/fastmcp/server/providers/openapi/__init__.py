"""OpenAPI provider for FastMCP.

This module provides OpenAPI integration for FastMCP through the Provider pattern.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.providers.openapi import OpenAPIProvider
    import httpx

    client = httpx.AsyncClient(base_url="https://api.example.com")
    provider = OpenAPIProvider(openapi_spec=spec, client=client)
    mcp = FastMCP("API Server", providers=[provider])
    ```
"""

from fastmcp.server.providers.openapi.components import (
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
)
from fastmcp.server.providers.openapi.provider import OpenAPIProvider
from fastmcp.server.providers.openapi.routing import (
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
