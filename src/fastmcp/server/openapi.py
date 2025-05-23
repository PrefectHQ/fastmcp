"""FastMCP server implementation for OpenAPI integration."""

from __future__ import annotations

import enum
import json
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern
from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp.types import EmbeddedResource, ImageContent, TextContent, ToolAnnotations
from pydantic.networks import AnyUrl

from fastmcp.exceptions import ToolError
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.server import FastMCP
from fastmcp.tools.tool import Tool, _convert_to_content
from fastmcp.utilities import openapi
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.openapi import (
    _combine_schemas,
    format_description_with_responses,
)

if TYPE_CHECKING:
    from fastmcp.server import Context

logger = get_logger(__name__)

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


class MCPType(enum.Enum):
    """Type of FastMCP component to create from a route.

    Enum values:
        TOOL: Convert the route to a callable Tool
        RESOURCE: Convert the route to a Resource (typically GET endpoints)
        RESOURCE_TEMPLATE: Convert the route to a ResourceTemplate (typically GET with path params)
        PROMPT: Convert the route to a Prompt (not yet implemented)
        EXCLUDE: Exclude the route from being converted to any MCP component
        IGNORE: Deprecated, use EXCLUDE instead
    """

    TOOL = "TOOL"
    RESOURCE = "RESOURCE"
    RESOURCE_TEMPLATE = "RESOURCE_TEMPLATE"
    PROMPT = "PROMPT"
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
    PROMPT = "PROMPT"
    IGNORE = "IGNORE"


@dataclass
class RouteMap:
    """Mapping configuration for HTTP routes to FastMCP component types."""

    methods: list[HttpMethod] | Literal["*"] = field(default="*")
    pattern: Pattern[str] | str = field(default=r".*")
    mcp_type: MCPType | None = field(default=None)
    route_type: RouteType | MCPType | None = field(default=None)
    tags: set[str] = field(default_factory=set)

    def __post_init__(self):
        """Validate and process the route map after initialization."""
        # Handle backward compatibility for route_type, deprecated in 2.5.0
        if self.mcp_type is None and self.route_type is not None:
            warnings.warn(
                "The 'route_type' parameter is deprecated and will be removed in a future version. "
                "Use 'mcp_type' instead with the appropriate MCPType value.",
                DeprecationWarning,
                stacklevel=2,
            )
            if isinstance(self.route_type, RouteType):
                warnings.warn(
                    "The RouteType class is deprecated and will be removed in a future version. "
                    "Use MCPType instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            # Check for the deprecated IGNORE value
            if self.route_type == RouteType.IGNORE:
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


# Default route mappings as a list, where order determines priority
DEFAULT_ROUTE_MAPPINGS = [
    # GET requests with path parameters go to ResourceTemplate
    RouteMap(
        methods=["GET"], pattern=r".*\{.*\}.*", mcp_type=MCPType.RESOURCE_TEMPLATE
    ),
    # GET requests without path parameters go to Resource
    RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
    # All other HTTP methods go to Tool
    RouteMap(methods="*", pattern=r".*", mcp_type=MCPType.TOOL),
]


def _determine_route_type(
    route: openapi.HTTPRoute,
    mappings: list[RouteMap],
) -> MCPType:
    """
    Determines the FastMCP component type based on the route and mappings.

    Args:
        route: HTTPRoute object
        mappings: List of RouteMap objects in priority order

    Returns:
        MCPType for this route
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
                return route_map.mcp_type

    # Default fallback
    return MCPType.TOOL


class OpenAPITool(Tool):
    """Tool implementation for OpenAPI endpoints."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        route: openapi.HTTPRoute,
        name: str,
        description: str,
        parameters: dict[str, Any],
        tags: set[str] = set(),
        timeout: float | None = None,
        annotations: ToolAnnotations | None = None,
        serializer: Callable[[Any], str] | None = None,
    ):
        super().__init__(
            name=name,
            description=description,
            parameters=parameters,
            fn=self._execute_request,  # We'll use an instance method instead of a global function
            tags=tags,
            annotations=annotations,
            serializer=serializer,
        )
        self._client = client
        self._route = route
        self._timeout = timeout

    def __repr__(self) -> str:
        """Custom representation to prevent recursion errors when printing."""
        return f"OpenAPITool(name={self.name!r}, method={self._route.method}, path={self._route.path})"

    async def _execute_request(self, *args, **kwargs):
        """Execute the HTTP request based on the route configuration."""
        context = kwargs.get("context")

        # Prepare URL
        path = self._route.path

        # Replace path parameters with values from kwargs
        # Path parameters should never be None as they're typically required
        # but we'll handle that case anyway
        path_params = {
            p.name: kwargs.get(p.name)
            for p in self._route.parameters
            if p.location == "path"
            and p.name in kwargs
            and kwargs.get(p.name) is not None
        }

        # Ensure all path parameters are provided
        required_path_params = {
            p.name
            for p in self._route.parameters
            if p.location == "path" and p.required
        }
        missing_params = required_path_params - path_params.keys()
        if missing_params:
            raise ToolError(f"Missing required path parameters: {missing_params}")

        for param_name, param_value in path_params.items():
            # Handle array path parameters with style 'simple' (comma-separated)
            # In OpenAPI, 'simple' is the default style for path parameters
            param_info = next(
                (p for p in self._route.parameters if p.name == param_name), None
            )

            if param_info and isinstance(param_value, list):
                # Check if schema indicates an array type
                schema = param_info.schema_
                is_array = schema.get("type") == "array"

                if is_array:
                    # Format array values as comma-separated string
                    # This follows the OpenAPI 'simple' style (default for path)
                    if all(
                        isinstance(item, str | int | float | bool)
                        for item in param_value
                    ):
                        # Handle simple array types
                        path = path.replace(
                            f"{{{param_name}}}", ",".join(str(v) for v in param_value)
                        )
                    else:
                        # Handle complex array types (containing objects/dicts)
                        try:
                            # Try to create a simple representation without Python syntax artifacts
                            formatted_parts = []
                            for item in param_value:
                                if isinstance(item, dict):
                                    # For objects, serialize key-value pairs
                                    item_parts = []
                                    for k, v in item.items():
                                        item_parts.append(f"{k}:{v}")
                                    formatted_parts.append(".".join(item_parts))
                                else:
                                    # Fallback for other complex types
                                    formatted_parts.append(str(item))

                            # Join parts with commas
                            formatted_value = ",".join(formatted_parts)
                            path = path.replace(f"{{{param_name}}}", formatted_value)
                        except Exception as e:
                            logger.warning(
                                f"Failed to format complex array path parameter '{param_name}': {e}"
                            )
                            # Fallback to string representation, but remove Python syntax artifacts
                            str_value = (
                                str(param_value)
                                .replace("[", "")
                                .replace("]", "")
                                .replace("'", "")
                                .replace('"', "")
                            )
                            path = path.replace(f"{{{param_name}}}", str_value)
                    continue

            # Default handling for non-array parameters or non-array schemas
            path = path.replace(f"{{{param_name}}}", str(param_value))

        # Prepare query parameters - filter out None and empty strings
        query_params = {}
        for p in self._route.parameters:
            if (
                p.location == "query"
                and p.name in kwargs
                and kwargs.get(p.name) is not None
                and kwargs.get(p.name) != ""
            ):
                param_value = kwargs.get(p.name)

                # Format array query parameters as comma-separated strings
                # following OpenAPI form style (default for query parameters)
                if isinstance(param_value, list) and p.schema_.get("type") == "array":
                    # Get explode parameter from schema, default is True for query parameters
                    # If explode is True, the array is serialized as separate parameters
                    # If explode is False, the array is serialized as a comma-separated string
                    explode = p.schema_.get("explode", True)

                    if explode:
                        # When explode=True, we pass the array directly, which HTTPX will serialize
                        # as multiple parameters with the same name
                        query_params[p.name] = param_value
                    else:
                        # For arrays of simple types (strings, numbers, etc.), join with commas
                        if all(
                            isinstance(item, str | int | float | bool)
                            for item in param_value
                        ):
                            query_params[p.name] = ",".join(str(v) for v in param_value)
                        else:
                            # For complex types, try to create a simpler representation
                            try:
                                # Try to create a simple string representation
                                formatted_parts = []
                                for item in param_value:
                                    if isinstance(item, dict):
                                        # For objects, serialize key-value pairs
                                        item_parts = []
                                        for k, v in item.items():
                                            item_parts.append(f"{k}:{v}")
                                        formatted_parts.append(".".join(item_parts))
                                    else:
                                        formatted_parts.append(str(item))

                                query_params[p.name] = ",".join(formatted_parts)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to format complex array query parameter '{p.name}': {e}"
                                )
                                # Fallback to string representation
                                query_params[p.name] = param_value
                else:
                    # Non-array parameters are passed as is
                    query_params[p.name] = param_value

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
                timeout=self._timeout,
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

    async def run(
        self, arguments: dict[str, Any]
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        """Run the tool with arguments and optional context."""
        response = await self._execute_request(**arguments)
        return _convert_to_content(response)


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
        timeout: float | None = None,
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
        self._timeout = timeout

    def __repr__(self) -> str:
        """Custom representation to prevent recursion errors when printing."""
        return f"OpenAPIResource(name={self.name!r}, uri={self.uri!r}, path={self._route.path})"

    async def read(self) -> str | bytes:
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

                    # Find the path parameter names from the route path
                    param_matches = re.findall(r"\{([^}]+)\}", path)
                    if param_matches:
                        # Reverse sorting from creation order (traversal is backwards)
                        param_matches.sort(reverse=True)
                        # Number of sent parameters is number of parts -1 (assuming first part is resource identifier)
                        expected_param_count = len(parts) - 1
                        # Map parameters from the end of the URI to the parameters in the path
                        # Last parameter in URI (parts[-1]) maps to last parameter in path, and so on
                        for i, param_name in enumerate(param_matches):
                            # Ensure we don't use resource identifier as parameter
                            if i < expected_param_count:
                                # Get values from the end of parts
                                param_value = parts[-1 - i]
                                path_params[param_name] = param_value

                    # Replace path parameters with their values
                    for param_name, param_value in path_params.items():
                        path = path.replace(f"{{{param_name}}}", str(param_value))

            # Filter any query parameters - get query parameters and filter out None/empty values
            query_params = {}
            for param in self._route.parameters:
                if param.location == "query" and hasattr(self, f"_{param.name}"):
                    value = getattr(self, f"_{param.name}")
                    if value is not None and value != "":
                        query_params[param.name] = value

            response = await self._client.request(
                method=self._route.method,
                url=path,
                params=query_params,
                timeout=self._timeout,
            )

            # Raise for 4xx/5xx responses
            response.raise_for_status()

            # Determine content type and return appropriate format
            content_type = response.headers.get("content-type", "").lower()

            if "application/json" in content_type:
                result = response.json()
                return json.dumps(result)
            elif any(ct in content_type for ct in ["text/", "application/xml"]):
                return response.text
            else:
                return response.content

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
        timeout: float | None = None,
    ):
        super().__init__(
            uri_template=uri_template,
            name=name,
            description=description,
            fn=lambda **kwargs: None,
            parameters=parameters,
            tags=tags,
        )
        self._client = client
        self._route = route
        self._timeout = timeout

    def __repr__(self) -> str:
        """Custom representation to prevent recursion errors when printing."""
        return f"OpenAPIResourceTemplate(name={self.name!r}, uri_template={self.uri_template!r}, path={self._route.path})"

    async def create_resource(
        self,
        uri: str,
        params: dict[str, Any],
        context: Context | None = None,
    ) -> Resource:
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
            description=self.description or f"Resource for {self._route.path}",
            mime_type="application/json",
            tags=set(self._route.tags or []),
            timeout=self._timeout,
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
                mcp_type=MCPType.RESOURCE_TEMPLATE
            ),
            # Map all analytics endpoints to Tool
            RouteMap(
                methods=["GET"],
                pattern=r".*/analytics/.*",
                mcp_type=MCPType.TOOL
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
        timeout: float | None = None,
        **settings: Any,
    ):
        """
        Initialize a FastMCP server from an OpenAPI schema.

        Args:
            openapi_spec: OpenAPI schema as a dictionary or file path
            client: httpx AsyncClient for making HTTP requests
            name: Optional name for the server
            route_maps: Optional list of RouteMap objects defining route mappings
            timeout: Optional timeout (in seconds) for all requests
            **settings: Additional settings for FastMCP
        """
        super().__init__(name=name or "OpenAPI FastMCP", **settings)

        self._client = client
        self._timeout = timeout

        # Keep track of names to detect collisions
        self._used_names = {"tools": set(), "resources": set(), "templates": set()}

        http_routes = openapi.parse_openapi_to_http_routes(openapi_spec)

        # Process routes
        route_maps = (route_maps or []) + DEFAULT_ROUTE_MAPPINGS
        for route in http_routes:
            # Determine route type based on mappings or default rules
            route_type = _determine_route_type(route, route_maps)

            # Generate a default name from the route
            component_name = self._generate_default_name(route, route_type)

            if route_type == MCPType.TOOL:
                self._create_openapi_tool(route, component_name)
            elif route_type == MCPType.RESOURCE:
                self._create_openapi_resource(route, component_name)
            elif route_type == MCPType.RESOURCE_TEMPLATE:
                self._create_openapi_template(route, component_name)
            elif route_type == MCPType.PROMPT:
                # Not implemented yet
                logger.warning(
                    f"PROMPT route type not implemented: {route.method} {route.path}"
                )
            elif route_type == MCPType.EXCLUDE:
                logger.info(f"Excluding route: {route.method} {route.path}")

        logger.info(f"Created FastMCP OpenAPI server with {len(http_routes)} routes")

    def _generate_default_name(
        self, route: openapi.HTTPRoute, mcp_type: MCPType
    ) -> str:
        """Generate a default name from the route path."""
        # First check for OpenAPI operationId which takes precedence
        if route.operation_id:
            return route.operation_id

        # For path-based naming, clean up the path
        path_parts = route.path.strip("/").split("/")

        # Remove path parameters (parts with {})
        clean_parts = []
        for part in path_parts:
            if part.startswith("{") and part.endswith("}"):
                # For templates, include parameter name without braces
                if mcp_type == MCPType.RESOURCE_TEMPLATE:
                    param_name = part[1:-1]  # Remove braces
                    clean_parts.append(param_name)
            else:
                clean_parts.append(part)

        # Join the parts
        resource_name = "_".join(clean_parts)

        # For tools, might be useful to keep the method for clarity on what it does
        if mcp_type == MCPType.TOOL:
            # Only include method if it helps distinguish (POST, PUT, PATCH, DELETE)
            # For GET we don't need the method as it's implied for resources
            if route.method != "GET":
                resource_name = f"{route.method.lower()}_{resource_name}"

        return resource_name

    def _get_unique_name(
        self, name: str, component_type: Literal["tools", "resources", "templates"]
    ) -> str:
        """
        Ensure the name is unique within its component type by appending numbers if needed.

        Args:
            name: The proposed name
            component_type: The type of component ("tools", "resources", or "templates")

        Returns:
            str: A unique name for the component
        """
        # Check if the name is already used
        if name not in self._used_names[component_type]:
            self._used_names[component_type].add(name)
            return name

        # Find the next available number suffix
        counter = 2
        while f"{name}_{counter}" in self._used_names[component_type]:
            counter += 1

        # Create the new name
        new_name = f"{name}_{counter}"
        logger.debug(
            f"Name collision detected: '{name}' already exists as a {component_type[:-1]}. "
            f"Using '{new_name}' instead."
        )

        self._used_names[component_type].add(new_name)
        return new_name

    def _create_openapi_tool(self, route: openapi.HTTPRoute, name: str):
        """Creates and registers an OpenAPITool with enhanced description."""
        combined_schema = _combine_schemas(route)

        # Get a unique tool name
        tool_name = self._get_unique_name(name, "tools")

        base_description = (
            route.description
            or route.summary
            or f"Executes {route.method} {route.path}"
        )

        # Format enhanced description with parameters and request body
        enhanced_description = format_description_with_responses(
            base_description=base_description,
            responses=route.responses,
            parameters=route.parameters,
            request_body=route.request_body,
        )

        tool = OpenAPITool(
            client=self._client,
            route=route,
            name=tool_name,
            description=enhanced_description,
            parameters=combined_schema,
            tags=set(route.tags or []),
            timeout=self._timeout,
        )
        # Register the tool by directly assigning to the tools dictionary
        self._tool_manager._tools[tool_name] = tool
        logger.debug(
            f"Registered TOOL: {tool_name} ({route.method} {route.path}) with tags: {route.tags}"
        )

    def _create_openapi_resource(self, route: openapi.HTTPRoute, name: str):
        """Creates and registers an OpenAPIResource with enhanced description."""
        # Get a unique resource name
        resource_name = self._get_unique_name(name, "resources")

        resource_uri = f"resource://openapi/{resource_name}"
        base_description = (
            route.description or route.summary or f"Represents {route.path}"
        )

        # Format enhanced description with parameters and request body
        enhanced_description = format_description_with_responses(
            base_description=base_description,
            responses=route.responses,
            parameters=route.parameters,
            request_body=route.request_body,
        )

        resource = OpenAPIResource(
            client=self._client,
            route=route,
            uri=resource_uri,
            name=resource_name,
            description=enhanced_description,
            tags=set(route.tags or []),
            timeout=self._timeout,
        )
        # Register the resource by directly assigning to the resources dictionary
        self._resource_manager._resources[str(resource.uri)] = resource
        logger.debug(
            f"Registered RESOURCE: {resource_uri} ({route.method} {route.path}) with tags: {route.tags}"
        )

    def _create_openapi_template(self, route: openapi.HTTPRoute, name: str):
        """Creates and registers an OpenAPIResourceTemplate with enhanced description."""
        # Get a unique template name
        template_name = self._get_unique_name(name, "templates")

        path_params = [p.name for p in route.parameters if p.location == "path"]
        path_params.sort()  # Sort for consistent URIs

        uri_template_str = f"resource://openapi/{template_name}"
        if path_params:
            uri_template_str += "/" + "/".join(f"{{{p}}}" for p in path_params)

        base_description = (
            route.description or route.summary or f"Template for {route.path}"
        )

        # Format enhanced description with parameters and request body
        enhanced_description = format_description_with_responses(
            base_description=base_description,
            responses=route.responses,
            parameters=route.parameters,
            request_body=route.request_body,
        )

        template_params_schema = {
            "type": "object",
            "properties": {
                p.name: {
                    **(p.schema_.copy() if isinstance(p.schema_, dict) else {}),
                    **(
                        {"description": p.description}
                        if p.description
                        and not (
                            isinstance(p.schema_, dict) and "description" in p.schema_
                        )
                        else {}
                    ),
                }
                for p in route.parameters
                if p.location == "path"
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
            timeout=self._timeout,
        )
        # Register the template by directly assigning to the templates dictionary
        self._resource_manager._templates[uri_template_str] = template
        logger.debug(
            f"Registered TEMPLATE: {uri_template_str} ({route.method} {route.path}) with tags: {route.tags}"
        )

    async def _mcp_call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Override the call_tool method to return the raw result without converting to content."""
        result = await self._tool_manager.call_tool(name, arguments)
        return result
