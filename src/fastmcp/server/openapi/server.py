"""FastMCPOpenAPI - backwards compatibility wrapper.

This class is deprecated. Use FastMCP with OpenAPIProvider instead:

    from fastmcp import FastMCP
    from fastmcp.server.providers.openapi import OpenAPIProvider
    import httpx

    client = httpx.AsyncClient(base_url="https://api.example.com")
    provider = OpenAPIProvider(openapi_spec=spec, client=client)
    mcp = FastMCP("My API Server", providers=[provider])
"""

from __future__ import annotations

import warnings
from typing import Any

import httpx

from fastmcp.server.providers.openapi import (
    ComponentFn,
    OpenAPIProvider,
    RouteMap,
    RouteMapFn,
)
from fastmcp.server.server import FastMCP


class FastMCPOpenAPI(FastMCP):
    """FastMCP server implementation that creates components from an OpenAPI schema.

    .. deprecated::
        Use FastMCP with OpenAPIProvider instead. This class will be
        removed in a future version.

    Example (deprecated):
        ```python
        from fastmcp.server.openapi import FastMCPOpenAPI
        import httpx

        server = FastMCPOpenAPI(
            openapi_spec=spec,
            client=httpx.AsyncClient(),
        )
        ```

    New approach:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.providers.openapi import OpenAPIProvider
        import httpx

        client = httpx.AsyncClient(base_url="https://api.example.com")
        provider = OpenAPIProvider(openapi_spec=spec, client=client)
        mcp = FastMCP("API Server", providers=[provider])
        ```
    """

    def __init__(
        self,
        openapi_spec: dict[str, Any],
        client: httpx.AsyncClient,
        name: str | None = None,
        route_maps: list[RouteMap] | None = None,
        route_map_fn: RouteMapFn | None = None,
        mcp_component_fn: ComponentFn | None = None,
        mcp_names: dict[str, str] | None = None,
        tags: set[str] | None = None,
        timeout: float | None = None,
        **settings: Any,
    ):
        """Initialize a FastMCP server from an OpenAPI schema.

        .. deprecated::
            Use FastMCP with OpenAPIProvider instead.

        Args:
            openapi_spec: OpenAPI schema as a dictionary
            client: httpx AsyncClient for making HTTP requests
            name: Optional name for the server
            route_maps: Optional list of RouteMap objects defining route mappings
            route_map_fn: Optional callable for advanced route type mapping
            mcp_component_fn: Optional callable for component customization
            mcp_names: Optional dictionary mapping operationId to component names
            tags: Optional set of tags to add to all components
            timeout: Optional timeout (in seconds) for all requests
            **settings: Additional settings for FastMCP
        """
        warnings.warn(
            "FastMCPOpenAPI is deprecated. Use FastMCP with OpenAPIProvider instead:\n"
            "    provider = OpenAPIProvider(openapi_spec=spec, client=client)\n"
            "    mcp = FastMCP('name', providers=[provider])",
            DeprecationWarning,
            stacklevel=2,
        )

        super().__init__(name=name or "OpenAPI FastMCP", **settings)

        # Store references for backwards compatibility
        self._client = client
        self._timeout = timeout
        self._mcp_component_fn = mcp_component_fn

        # Create provider with the client
        provider = OpenAPIProvider(
            openapi_spec=openapi_spec,
            client=client,
            route_maps=route_maps,
            route_map_fn=route_map_fn,
            mcp_component_fn=mcp_component_fn,
            mcp_names=mcp_names,
            tags=tags,
            timeout=timeout,
        )

        self.add_provider(provider)

        # Expose internal attributes for backwards compatibility
        self._spec = provider._spec
        self._director = provider._director


# Export public symbols
__all__ = [
    "FastMCPOpenAPI",
]
