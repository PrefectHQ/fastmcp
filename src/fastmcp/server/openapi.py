"""FastMCP server implementation for OpenAPI integration."""

import enum
import json
import re
from dataclasses import dataclass
from re import Pattern
from typing import Any, Literal

import httpx
from mcp.types import TextContent
from pydantic.networks import AnyUrl

from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.server import FastMCP
from fastmcp.tools.tool import Tool
from fastmcp.utilities import openapi
from fastmcp.utilities.func_metadata import func_metadata
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.openapi import (
    _combine_schemas,
    format_description_with_responses,
)

logger = get_logger(__name__)

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


class RouteType(enum.Enum):
    """Type of FastMCP component to create from a route."""

    TOOL = "TOOL"
    RESOURCE = "RESOURCE"
    RESOURCE_TEMPLATE = "RESOURCE_TEMPLATE"
    PROMPT = "PROMPT"
    IGNORE = "IGNORE"


@dataclass
class RouteMap:
    """Mapping configuration for HTTP routes to FastMCP component types."""

    methods: list[HttpMethod]
    pattern: Pattern[str] | str
    route_type: RouteType


# Default route mappings as a list, where order determines priority
DEFAULT_ROUTE_MAPPINGS = [
    # GET requests with path parameters go to ResourceTemplate
    RouteMap(
        methods=["GET"], pattern=r".*\{.*\}.*", route_type=RouteType.RESOURCE_TEMPLATE
    ),
    # GET requests without path parameters go to Resource
    RouteMap(methods=["GET"], pattern=r".*", route_type=RouteType.RESOURCE),
    # All other HTTP methods go to Tool
    RouteMap(
        methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        pattern=r".*",
        route_type=RouteType.TOOL,
    ),
]


def _determine_route_type(
    route: openapi.HTTPRoute,
    mappings: list[RouteMap],
) -> RouteType:
    """
    Determines the FastMCP component type based on the route and mappings.

    Args:
        route: HTTPRoute object
        mappings: List of RouteMap objects in priority order

    Returns:
        RouteType for this route
    """
    # Check mappings in priority order (first match wins)
    for route_map in mappings:
        # Check if the HTTP method matches
        if route.method in route_map.methods:
            # Handle both string patterns and compiled Pattern objects
            if isinstance(route_map.pattern, Pattern):
                pattern_matches = route_map.pattern.search(route.path)
            else:
                pattern_matches = re.search(route_map.pattern, route.path)

            if pattern_matches:
                logger.debug(
                    f"Route {route.method} {route.path} matched mapping to {route_map.route_type.name}"
                )
                return route_map.route_type

    # Default fallback
    return RouteType.TOOL


# Placeholder function to provide function metadata
async def _openapi_passthrough(*args, **kwargs):
    """Placeholder function for OpenAPI endpoints."""
    # This is kept for metadata generation purposes
    pass


class OpenAPITool(Tool):
    """Tool implementation for OpenAPI endpoints."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        route: openapi.HTTPRoute,
        name: str,
        description: str,
        parameters: dict[str, Any],
        fn_metadata: Any,
        is_async: bool = True,
        tags: set[str] = set(),
    ):
        super().__init__(
            name=name,
            description=description,
            parameters=parameters,
            fn=self._execute_request,  # We'll use an instance method instead of a global function
            fn_metadata=fn_metadata,
            is_async=is_async,
            context_kwarg="context",  # Default context keyword argument
            tags=tags,
        )
        self._client = client
        self._route = route

    async def _execute_request(self, *args, **kwargs):
        """Execute the HTTP request based on the route configuration."""
        context = kwargs.get("context")

        # Prepare URL
        path = self._route.path

        # Replace path parameters with values from kwargs
        path_params = {
            p.name: kwargs.get(p.name)
            for p in self._route.parameters
            if p.location == "path"
        }
        for param_name, param_value in path_params.items():
            path = path.replace(f"{{{param_name}}}", str(param_value))

        # Prepare query parameters
        query_params = {
            p.name: kwargs.get(p.name)
            for p in self._route.parameters
            if p.location == "query" and p.name in kwargs
        }

        # Prepare headers - fix typing by ensuring all values are strings
        headers = {}
        for p in self._route.parameters:
            if (
                p.location == "header"
                and p.name in kwargs
                and kwargs[p.name] is not None
            ):
                headers[p.name] = str(kwargs[p.name])

        # Prepare request body
        json_data = None
        if self._route.request_body and self._route.request_body.content_schema:
            # Extract body parameters, excluding path/query/header params that were already used
            path_query_header_params = {
                p.name
                for p in self._route.parameters
                if p.location in ("path", "query", "header")
            }
            body_params = {
                k: v
                for k, v in kwargs.items()
                if k not in path_query_header_params and k != "context"
            }

            if body_params:
                json_data = body_params

        # Log the request details if a context is available
        if context:
            try:
                await context.info(f"Making {self._route.method} request to {path}")
            except (ValueError, AttributeError):
                # Silently continue if context logging is not available
                pass

        # Execute the request
        try:
            response = await self._client.request(
                method=self._route.method,
                url=path,
                params=query_params,
                headers=headers,
                json=json_data,
                timeout=30.0,  # Default timeout
            )

            # Raise for 4xx/5xx responses
            response.raise_for_status()

            # Try to parse as JSON first
            try:
                return response.json()
            except (json.JSONDecodeError, ValueError):
                # Return text content if not JSON
                return response.text

        except httpx.HTTPStatusError as e:
            # Handle HTTP errors (4xx, 5xx)
            error_message = (
                f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
            )
            try:
                error_data = e.response.json()
                error_message += f" - {error_data}"
            except (json.JSONDecodeError, ValueError):
                if e.response.text:
                    error_message += f" - {e.response.text}"

            raise ValueError(error_message)

        except httpx.RequestError as e:
            # Handle request errors (connection, timeout, etc.)
            raise ValueError(f"Request error: {str(e)}")

    async def run(self, arguments: dict[str, Any], context: Any = None) -> Any:
        """Run the tool with arguments and optional context."""
        return await self._execute_request(**arguments, context=context)


class OpenAPIResource(Resource):
    """Resource implementation for OpenAPI endpoints."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        route: openapi.HTTPRoute,
        uri: str,
        name: str,
        description: str,
        mime_type: str = "application/json",
        tags: set[str] = set(),
    ):
        super().__init__(
            uri=AnyUrl(uri),  # Convert string to AnyUrl
            name=name,
            description=description,
            mime_type=mime_type,
            tags=tags,
        )
        self._client = client
        self._route = route

    async def read(self) -> str:
        """Fetch the resource data by making an HTTP request."""
        try:
            # Extract path parameters from the URI if present
            path = self._route.path
            resource_uri = str(self.uri)

            # If this is a templated resource, extract path parameters from the URI
            if "{" in path and "}" in path:
                # Extract the resource ID from the URI (the last part after the last slash)
                parts = resource_uri.split("/")
                if len(parts) > 1:
                    # Find all path parameters in the route path
                    path_params = {}

                    # Extract parameters from the URI
                    param_value = parts[
                        -1
                    ]  # The last part contains the parameter value

                    # Find the path parameter name from the route path
                    param_matches = re.findall(r"\{([^}]+)\}", path)
                    if param_matches:
                        # Assume the last parameter in the URI is for the first path parameter in the route
                        path_param_name = param_matches[0]
                        path_params[path_param_name] = param_value

                    # Replace path parameters with their values
                    for param_name, param_value in path_params.items():
                        path = path.replace(f"{{{param_name}}}", str(param_value))

            response = await self._client.request(
                method=self._route.method,
                url=path,
                timeout=30.0,  # Default timeout
            )

            # Raise for 4xx/5xx responses
            response.raise_for_status()

            # Return response content based on mime type
            if self.mime_type == "application/json":
                try:
                    return response.json()
                except (json.JSONDecodeError, ValueError):
                    # Fallback to returning the text
                    return response.text
            else:
                return response.text

        except httpx.HTTPStatusError as e:
            # Handle HTTP errors (4xx, 5xx)
            error_message = (
                f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
            )
            try:
                error_data = e.response.json()
                error_message += f" - {error_data}"
            except (json.JSONDecodeError, ValueError):
                if e.response.text:
                    error_message += f" - {e.response.text}"

            raise ValueError(error_message)

        except httpx.RequestError as e:
            # Handle request errors (connection, timeout, etc.)
            raise ValueError(f"Request error: {str(e)}")


class OpenAPIResourceTemplate(ResourceTemplate):
    """Resource template implementation for OpenAPI endpoints."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        route: openapi.HTTPRoute,
        uri_template: str,
        name: str,
        description: str,
        parameters: dict[str, Any],
        tags: set[str] = set(),
    ):
        super().__init__(
            uri_template=uri_template,
            name=name,
            description=description,
            fn=self._create_resource_fn,
            parameters=parameters,
            tags=tags,
        )
        self._client = client
        self._route = route

    async def _create_resource_fn(self, **kwargs):
        """Create a resource with parameters."""
        # Prepare the path with parameters
        path = self._route.path
        for param_name, param_value in kwargs.items():
            path = path.replace(f"{{{param_name}}}", str(param_value))

        try:
            response = await self._client.request(
                method=self._route.method,
                url=path,
                timeout=30.0,  # Default timeout
            )

            # Raise for 4xx/5xx responses
            response.raise_for_status()

            # Determine the mime type from the response
            content_type = response.headers.get("content-type", "application/json")
            mime_type = content_type.split(";")[0].strip()

            # Return the appropriate data
            if mime_type == "application/json":
                try:
                    return response.json()
                except (json.JSONDecodeError, ValueError):
                    return response.text
            else:
                return response.text

        except httpx.HTTPStatusError as e:
            error_message = (
                f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
            )
            try:
                error_data = e.response.json()
                error_message += f" - {error_data}"
            except (json.JSONDecodeError, ValueError):
                if e.response.text:
                    error_message += f" - {e.response.text}"

            raise ValueError(error_message)

        except httpx.RequestError as e:
            raise ValueError(f"Request error: {str(e)}")

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a resource with the given parameters."""
        # Generate a URI for this resource instance
        uri_parts = []
        for key, value in params.items():
            uri_parts.append(f"{key}={value}")

        # Create and return a resource
        return OpenAPIResource(
            client=self._client,
            route=self._route,
            uri=uri,
            name=f"{self.name}-{'-'.join(uri_parts)}",
            description=self.description
            or f"Resource for {self._route.path}",  # Provide default if None
            mime_type="application/json",  # Default, will be updated when read
            tags=set(self._route.tags or []),
        )


class FastMCPOpenAPI(FastMCP):
    """
    FastMCP server implementation that creates components from an OpenAPI schema.

    This class parses an OpenAPI specification and creates appropriate FastMCP components
    (Tools, Resources, ResourceTemplates) based on route mappings.

    Example:
        ```python
        from fastmcp.server.openapi import FastMCPOpenAPI, RouteMap, RouteType
        import httpx

        # Define custom route mappings
        custom_mappings = [
            # Map all user-related endpoints to ResourceTemplate
            RouteMap(
                methods=["GET", "POST", "PATCH"],
                pattern=r".*/users/.*",
                route_type=RouteType.RESOURCE_TEMPLATE
            ),
            # Map all analytics endpoints to Tool
            RouteMap(
                methods=["GET"],
                pattern=r".*/analytics/.*",
                route_type=RouteType.TOOL
            ),
        ]

        # Create server with custom mappings
        server = FastMCPOpenAPI(
            openapi_spec=spec,
            client=httpx.AsyncClient(),
            name="API Server",
            route_maps=custom_mappings,
        )
        ```
    """

    def __init__(
        self,
        openapi_spec: dict[str, Any],
        client: httpx.AsyncClient,
        name: str | None = None,
        route_maps: list[RouteMap] | None = None,
        **settings: Any,
    ):
        """
        Initialize a FastMCP server from an OpenAPI schema.

        Args:
            openapi_spec: OpenAPI schema as a dictionary or file path
            client: httpx AsyncClient for making HTTP requests
            name: Optional name for the server
            route_maps: Optional list of RouteMap objects defining route mappings
            default_mime_type: Default MIME type for resources
            **settings: Additional settings for FastMCP
        """
        super().__init__(name=name or "OpenAPI FastMCP", **settings)

        self._client = client

        http_routes = openapi.parse_openapi_to_http_routes(openapi_spec)

        # Process routes
        route_maps = (route_maps or []) + DEFAULT_ROUTE_MAPPINGS
        for route in http_routes:
            # Determine route type based on mappings or default rules
            route_type = _determine_route_type(route, route_maps)

            # Use operation_id if available, otherwise generate a name
            operation_id = route.operation_id
            if not operation_id:
                # Generate operation ID from method and path
                path_parts = route.path.strip("/").split("/")
                path_name = "_".join(p for p in path_parts if not p.startswith("{"))
                operation_id = f"{route.method.lower()}_{path_name}"

            if route_type == RouteType.TOOL:
                self._create_openapi_tool(route, operation_id)
            elif route_type == RouteType.RESOURCE:
                self._create_openapi_resource(route, operation_id)
            elif route_type == RouteType.RESOURCE_TEMPLATE:
                self._create_openapi_template(route, operation_id)
            elif route_type == RouteType.PROMPT:
                # Not implemented yet
                logger.warning(
                    f"PROMPT route type not implemented: {route.method} {route.path}"
                )
            elif route_type == RouteType.IGNORE:
                logger.info(f"Ignoring route: {route.method} {route.path}")

        logger.info(f"Created FastMCP OpenAPI server with {len(http_routes)} routes")

    def _create_openapi_tool(self, route: openapi.HTTPRoute, operation_id: str):
        """Creates and registers an OpenAPITool with enhanced description."""
        combined_schema = _combine_schemas(route)
        tool_name = operation_id
        base_description = (
            route.description
            or route.summary
            or f"Executes {route.method} {route.path}"
        )

        # Format enhanced description
        enhanced_description = format_description_with_responses(
            base_description=base_description,
            responses=route.responses,
        )

        tool = OpenAPITool(
            client=self._client,
            route=route,
            name=tool_name,
            description=enhanced_description,
            parameters=combined_schema,
            fn_metadata=func_metadata(_openapi_passthrough),
            is_async=True,
            tags=set(route.tags or []),
        )
        # Register the tool by directly assigning to the tools dictionary
        self._tool_manager._tools[tool_name] = tool
        logger.debug(
            f"Registered TOOL: {tool_name} ({route.method} {route.path}) with tags: {route.tags}"
        )

    def _create_openapi_resource(self, route: openapi.HTTPRoute, operation_id: str):
        """Creates and registers an OpenAPIResource with enhanced description."""
        resource_name = operation_id
        resource_uri = f"resource://openapi/{resource_name}"
        base_description = (
            route.description or route.summary or f"Represents {route.path}"
        )

        # Format enhanced description
        enhanced_description = format_description_with_responses(
            base_description=base_description,
            responses=route.responses,
        )

        resource = OpenAPIResource(
            client=self._client,
            route=route,
            uri=resource_uri,
            name=resource_name,
            description=enhanced_description,
            tags=set(route.tags or []),
        )
        # Register the resource by directly assigning to the resources dictionary
        self._resource_manager._resources[str(resource.uri)] = resource
        logger.debug(
            f"Registered RESOURCE: {resource_uri} ({route.method} {route.path}) with tags: {route.tags}"
        )

    def _create_openapi_template(self, route: openapi.HTTPRoute, operation_id: str):
        """Creates and registers an OpenAPIResourceTemplate with enhanced description."""
        template_name = operation_id
        path_params = [p.name for p in route.parameters if p.location == "path"]
        path_params.sort()  # Sort for consistent URIs

        uri_template_str = f"resource://openapi/{template_name}"
        if path_params:
            uri_template_str += "/" + "/".join(f"{{{p}}}" for p in path_params)

        base_description = (
            route.description or route.summary or f"Template for {route.path}"
        )

        # Format enhanced description
        enhanced_description = format_description_with_responses(
            base_description=base_description,
            responses=route.responses,
        )

        template_params_schema = {
            "type": "object",
            "properties": {
                p.name: p.schema_ for p in route.parameters if p.location == "path"
            },
            "required": [
                p.name for p in route.parameters if p.location == "path" and p.required
            ],
        }

        template = OpenAPIResourceTemplate(
            client=self._client,
            route=route,
            uri_template=uri_template_str,
            name=template_name,
            description=enhanced_description,
            parameters=template_params_schema,
            tags=set(route.tags or []),
        )
        # Register the template by directly assigning to the templates dictionary
        self._resource_manager._templates[uri_template_str] = template
        logger.debug(
            f"Registered TEMPLATE: {uri_template_str} ({route.method} {route.path}) with tags: {route.tags}"
        )

    async def _mcp_call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Override the call_tool method to return the raw result without converting to content."""

        context = self.get_context()
        result = await self._tool_manager.call_tool(name, arguments, context=context)

        # For other tools, ensure the response is wrapped in TextContent
        if isinstance(result, dict | str):
            if isinstance(result, dict):
                result_text = json.dumps(result)
            else:
                result_text = result
            return [TextContent(text=result_text, type="text")]

        return result
