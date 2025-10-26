"""FastMCP server implementation for OpenAPI integration."""

from __future__ import annotations

import enum
import json
import re
import warnings
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern
from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp.types import ToolAnnotations
from pydantic.networks import AnyUrl

import fastmcp
from fastmcp.exceptions import ToolError
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.server import FastMCP
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities import openapi
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.openapi import (
    HTTPRoute,
    _combine_schemas,
    extract_output_schema_from_responses,
    format_array_parameter,
    format_deep_object_parameter,
    format_description_with_responses,
)

if TYPE_CHECKING:
    from fastmcp.server import Context

logger = get_logger(__name__)

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _slugify(text: str) -> str:
    """
    Convert text to a URL-friendly slug format that only contains lowercase
    letters, uppercase letters, numbers, and underscores.
    """
    if not text:
        return ""

    # Replace spaces and common separators with underscores
    slug = re.sub(r"[\s\-\.]+", "_", text)

    # Remove non-alphanumeric characters except underscores
    slug = re.sub(r"[^a-zA-Z0-9_]", "", slug)

    # Remove multiple consecutive underscores
    slug = re.sub(r"_+", "_", slug)

    # Remove leading/trailing underscores
    slug = slug.strip("_")

    return slug


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
    route: openapi.HTTPRoute,
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


class OpenAPITool(Tool):
    """Tool implementation for OpenAPI endpoints."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        route: openapi.HTTPRoute,
        name: str,
        description: str,
        parameters: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
        tags: set[str] | None = None,
        timeout: float | None = None,
        annotations: ToolAnnotations | None = None,
        serializer: Callable[[Any], str] | None = None,
    ):
        super().__init__(
            name=name,
            description=description,
            parameters=parameters,
            output_schema=output_schema,
            tags=tags or set(),
            annotations=annotations,
            serializer=serializer,
        )
        self._client = client
        self._route = route
        self._timeout = timeout

    def __repr__(self) -> str:
        """Custom representation to prevent recursion errors when printing."""
        return f"OpenAPITool(name={self.name!r}, method={self._route.method}, path={self._route.path})"

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the HTTP request based on the route configuration."""

        # Create mapping from suffixed parameter names back to original names and locations
        # This handles parameter collisions where suffixes were added during schema generation
        param_mapping = {}  # suffixed_name -> (original_name, location)

        # First, check if we have request body properties to detect collisions
        body_props = set()
        if self._route.request_body and self._route.request_body.content_schema:
            content_type = next(iter(self._route.request_body.content_schema))
            body_schema = self._route.request_body.content_schema[content_type]
            body_props = set(body_schema.get("properties", {}).keys())

        # Build parameter mapping for potentially suffixed parameters
        for param in self._route.parameters:
            original_name = param.name
            suffixed_name = f"{param.name}__{param.location}"

            # If parameter name collides with body property, it would have been suffixed
            if param.name in body_props:
                param_mapping[suffixed_name] = (original_name, param.location)
            # Also map original name for backward compatibility when no collision
            param_mapping[original_name] = (original_name, param.location)

        # Prepare URL
        path = self._route.path

        # Replace path parameters with values from arguments
        # Look for both original and suffixed parameter names
        path_params = {}
        for p in self._route.parameters:
            if p.location == "path":
                # Try suffixed name first, then original name
                suffixed_name = f"{p.name}__{p.location}"
                if (
                    suffixed_name in arguments
                    and arguments.get(suffixed_name) is not None
                ):
                    path_params[p.name] = arguments[suffixed_name]
                elif p.name in arguments and arguments.get(p.name) is not None:
                    path_params[p.name] = arguments[p.name]

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
                    formatted_value = format_array_parameter(
                        param_value, param_name, is_query_parameter=False
                    )
                    path = path.replace(f"{{{param_name}}}", str(formatted_value))
                    continue

            # Default handling for non-array parameters or non-array schemas
            path = path.replace(f"{{{param_name}}}", str(param_value))

        # Prepare query parameters - filter out None and empty strings
        query_params = {}
        for p in self._route.parameters:
            if p.location == "query":
                # Try suffixed name first, then original name
                suffixed_name = f"{p.name}__{p.location}"
                param_value = None

                suffixed_value = arguments.get(suffixed_name)
                if (
                    suffixed_name in arguments
                    and suffixed_value is not None
                    and suffixed_value != ""
                    and not (
                        isinstance(suffixed_value, list | dict)
                        and len(suffixed_value) == 0
                    )
                ):
                    param_value = arguments[suffixed_name]
                else:
                    name_value = arguments.get(p.name)
                    if (
                        p.name in arguments
                        and name_value is not None
                        and name_value != ""
                        and not (
                            isinstance(name_value, list | dict) and len(name_value) == 0
                        )
                    ):
                        param_value = arguments[p.name]

                if param_value is not None:
                    # Handle different parameter styles and types
                    param_style = (
                        p.style or "form"
                    )  # Default style for query parameters is "form"
                    param_explode = (
                        p.explode if p.explode is not None else True
                    )  # Default explode for query is True

                    # Handle deepObject style for object parameters
                    if (
                        param_style == "deepObject"
                        and isinstance(param_value, dict)
                        and len(param_value) > 0
                    ):
                        if param_explode:
                            # deepObject with explode=true: object properties become separate parameters
                            # e.g., target[id]=123&target[type]=user
                            deep_obj_params = format_deep_object_parameter(
                                param_value, p.name
                            )
                            query_params.update(deep_obj_params)
                        else:
                            # deepObject with explode=false is not commonly used, fallback to JSON
                            logger.warning(
                                f"deepObject style with explode=false for parameter '{p.name}' is not standard. "
                                f"Using JSON serialization fallback."
                            )
                            query_params[p.name] = json.dumps(param_value)
                    # Handle array parameters with form style (default)
                    elif (
                        isinstance(param_value, list)
                        and p.schema_.get("type") == "array"
                        and len(param_value) > 0
                    ):
                        if param_explode:
                            # When explode=True, we pass the array directly, which HTTPX will serialize
                            # as multiple parameters with the same name
                            query_params[p.name] = param_value
                        else:
                            # Format array as comma-separated string when explode=False
                            formatted_value = format_array_parameter(
                                param_value, p.name, is_query_parameter=True
                            )
                            query_params[p.name] = formatted_value
                    else:
                        # Non-array, non-deepObject parameters are passed as is
                        query_params[p.name] = param_value

        # Prepare headers - fix typing by ensuring all values are strings
        headers = {}

        # Start with OpenAPI-defined header parameters
        openapi_headers = {}
        for p in self._route.parameters:
            if p.location == "header":
                # Try suffixed name first, then original name
                suffixed_name = f"{p.name}__{p.location}"
                param_value = None

                if (
                    suffixed_name in arguments
                    and arguments.get(suffixed_name) is not None
                ):
                    param_value = arguments[suffixed_name]
                elif p.name in arguments and arguments.get(p.name) is not None:
                    param_value = arguments[p.name]

                if param_value is not None:
                    openapi_headers[p.name.lower()] = str(param_value)
        headers.update(openapi_headers)

        # Add headers from the current MCP client HTTP request (these take precedence)
        mcp_headers = get_http_headers()
        headers.update(mcp_headers)

        # Prepare request body
        json_data = None
        if self._route.request_body and self._route.request_body.content_schema:
            # Extract body parameters with collision-aware logic
            # Exclude all parameter names that belong to path/query/header locations
            params_to_exclude = set()

            for p in self._route.parameters:
                if (
                    p.name in body_props
                ):  # This parameter had a collision, so it was suffixed
                    params_to_exclude.add(f"{p.name}__{p.location}")
                else:  # No collision, parameter keeps original name but should still be excluded from body
                    params_to_exclude.add(p.name)

            body_params = {
                k: v for k, v in arguments.items() if k not in params_to_exclude
            }

            if body_params:
                json_data = body_params

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
                result = response.json()

                # Handle structured content based on output schema, if any
                structured_output = None
                if self.output_schema is not None:
                    if self.output_schema.get("x-fastmcp-wrap-result"):
                        # Schema says wrap - always wrap in result key
                        structured_output = {"result": result}
                    else:
                        structured_output = result
                # If no output schema, use fallback logic for backward compatibility
                elif not isinstance(result, dict):
                    structured_output = {"result": result}
                else:
                    structured_output = result

                return ToolResult(structured_content=structured_output)
            except json.JSONDecodeError:
                return ToolResult(content=response.text)

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

            raise ValueError(error_message) from e

        except httpx.RequestError as e:
            # Handle request errors (connection, timeout, etc.)
            raise ValueError(f"Request error: {e!s}") from e


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
        tags: set[str] | None = None,
        timeout: float | None = None,
    ):
        if tags is None:
            tags = set()
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

            # Prepare headers from MCP client request if available
            headers = {}
            mcp_headers = get_http_headers()
            headers.update(mcp_headers)

            response = await self._client.request(
                method=self._route.method,
                url=path,
                params=query_params,
                headers=headers,
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

            raise ValueError(error_message) from e

        except httpx.RequestError as e:
            # Handle request errors (connection, timeout, etc.)
            raise ValueError(f"Request error: {e!s}") from e


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
        tags: set[str] | None = None,
        timeout: float | None = None,
    ):
        if tags is None:
            tags = set()
        super().__init__(
            uri_template=uri_template,
            name=name,
            description=description,
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
        from fastmcp.server.openapi import FastMCPOpenAPI, RouteMap, MCPType
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

        # Create server with custom mappings and route mapper
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
        route_map_fn: RouteMapFn | None = None,
        mcp_component_fn: ComponentFn | None = None,
        mcp_names: dict[str, str] | None = None,
        tags: set[str] | None = None,
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
            route_map_fn: Optional callable for advanced route type mapping.
                Receives (route, mcp_type) and returns MCPType or None.
                Called on every route, including excluded ones.
            mcp_component_fn: Optional callable for component customization.
                Receives (route, component) and can modify the component in-place.
                Called on every created component.
            mcp_names: Optional dictionary mapping operationId to desired component names.
                If an operationId is not in the dictionary, falls back to using the
                operationId up to the first double underscore. If no operationId exists,
                falls back to slugified summary or path-based naming.
                All names are truncated to 56 characters maximum.
            tags: Optional set of tags to add to all components. Components always receive any tags
                from the route.
            timeout: Optional timeout (in seconds) for all requests
            **settings: Additional settings for FastMCP
        """
        super().__init__(name=name or "OpenAPI FastMCP", **settings)

        self._client = client
        self._timeout = timeout
        self._mcp_component_fn = mcp_component_fn

        # Keep track of names to detect collisions
        self._used_names = {
            "tool": Counter(),
            "resource": Counter(),
            "resource_template": Counter(),
            "prompt": Counter(),
        }

        http_routes = openapi.parse_openapi_to_http_routes(openapi_spec)

        # Process routes
        num_excluded = 0
        route_maps = (route_maps or []) + DEFAULT_ROUTE_MAPPINGS
        for route in http_routes:
            # Determine route type based on mappings or default rules
            route_map = _determine_route_type(route, route_maps)

            # TODO: remove this once RouteType is removed and mcp_type is typed as MCPType without | None
            assert route_map.mcp_type is not None
            route_type = route_map.mcp_type

            # Call route_map_fn if provided
            if route_map_fn is not None:
                try:
                    result = route_map_fn(route, route_type)
                    if result is not None:
                        route_type = result
                        logger.debug(
                            f"Route {route.method} {route.path} mapping customized by route_map_fn: "
                            f"type={route_type.name}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Error in route_map_fn for {route.method} {route.path}: {e}. "
                        f"Using default values."
                    )

            # Generate a default name from the route
            component_name = self._generate_default_name(route, mcp_names)

            route_tags = set(route.tags) | route_map.mcp_tags | (tags or set())

            if route_type == MCPType.TOOL:
                self._create_openapi_tool(route, component_name, tags=route_tags)
            elif route_type == MCPType.RESOURCE:
                self._create_openapi_resource(route, component_name, tags=route_tags)
            elif route_type == MCPType.RESOURCE_TEMPLATE:
                self._create_openapi_template(route, component_name, tags=route_tags)
            elif route_type == MCPType.EXCLUDE:
                logger.info(f"Excluding route: {route.method} {route.path}")
                num_excluded += 1

        logger.info(
            f"Created FastMCP OpenAPI server with {len(http_routes) - num_excluded} routes"
        )

    def _generate_default_name(
        self, route: openapi.HTTPRoute, mcp_names_map: dict[str, str] | None = None
    ) -> str:
        """Generate a default name from the route using the configured strategy."""
        name = ""
        mcp_names_map = mcp_names_map or {}

        # First check if there's a custom mapping for this operationId
        if route.operation_id:
            if route.operation_id in mcp_names_map:
                name = mcp_names_map[route.operation_id]
            else:
                # If there's a double underscore in the operationId, use the first part
                name = route.operation_id.split("__")[0]
        else:
            name = route.summary or f"{route.method}_{route.path}"

        name = _slugify(name)

        # Truncate to 56 characters maximum
        if len(name) > 56:
            name = name[:56]

        return name

    def _get_unique_name(
        self,
        name: str,
        component_type: Literal["tool", "resource", "resource_template", "prompt"],
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
        self._used_names[component_type][name] += 1
        if self._used_names[component_type][name] == 1:
            return name

        else:
            # Create the new name
            new_name = f"{name}_{self._used_names[component_type][name]}"
            logger.debug(
                f"Name collision detected: '{name}' already exists as a {component_type[:-1]}. "
                f"Using '{new_name}' instead."
            )

        return new_name

    def _create_openapi_tool(
        self,
        route: openapi.HTTPRoute,
        name: str,
        tags: set[str],
    ):
        """Creates and registers an OpenAPITool with enhanced description."""
        combined_schema = _combine_schemas(route)

        # Extract output schema from OpenAPI responses
        output_schema = extract_output_schema_from_responses(
            route.responses, route.schema_definitions, route.openapi_version
        )

        # Get a unique tool name
        tool_name = self._get_unique_name(name, "tool")

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
            output_schema=output_schema,
            tags=set(route.tags or []) | tags,
            timeout=self._timeout,
        )

        # Call component_fn if provided
        if self._mcp_component_fn is not None:
            try:
                self._mcp_component_fn(route, tool)
                logger.debug(f"Tool {tool_name} customized by component_fn")
            except Exception as e:
                logger.warning(
                    f"Error in component_fn for tool {tool_name}: {e}. "
                    f"Using component as-is."
                )

        # Use the potentially modified tool name as the registration key
        final_tool_name = tool.name

        # Register the tool by directly assigning to the tools dictionary
        self._tool_manager._tools[final_tool_name] = tool
        logger.debug(
            f"Registered TOOL: {final_tool_name} ({route.method} {route.path}) with tags: {route.tags}"
        )

    def _create_openapi_resource(
        self,
        route: openapi.HTTPRoute,
        name: str,
        tags: set[str],
    ):
        """Creates and registers an OpenAPIResource with enhanced description."""
        # Get a unique resource name
        resource_name = self._get_unique_name(name, "resource")

        resource_uri = f"resource://{resource_name}"
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
            tags=set(route.tags or []) | tags,
            timeout=self._timeout,
        )

        # Call component_fn if provided
        if self._mcp_component_fn is not None:
            try:
                self._mcp_component_fn(route, resource)
                logger.debug(f"Resource {resource_uri} customized by component_fn")
            except Exception as e:
                logger.warning(
                    f"Error in component_fn for resource {resource_uri}: {e}. "
                    f"Using component as-is."
                )

        # Use the potentially modified resource URI as the registration key
        final_resource_uri = str(resource.uri)

        # Register the resource by directly assigning to the resources dictionary
        self._resource_manager._resources[final_resource_uri] = resource
        logger.debug(
            f"Registered RESOURCE: {final_resource_uri} ({route.method} {route.path}) with tags: {route.tags}"
        )

    def _create_openapi_template(
        self,
        route: openapi.HTTPRoute,
        name: str,
        tags: set[str],
    ):
        """Creates and registers an OpenAPIResourceTemplate with enhanced description."""
        # Get a unique template name
        template_name = self._get_unique_name(name, "resource_template")

        path_params = [p.name for p in route.parameters if p.location == "path"]
        path_params.sort()  # Sort for consistent URIs

        uri_template_str = f"resource://{template_name}"
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
            tags=set(route.tags or []) | tags,
            timeout=self._timeout,
        )

        # Call component_fn if provided
        if self._mcp_component_fn is not None:
            try:
                self._mcp_component_fn(route, template)
                logger.debug(f"Template {uri_template_str} customized by component_fn")
            except Exception as e:
                logger.warning(
                    f"Error in component_fn for template {uri_template_str}: {e}. "
                    f"Using component as-is."
                )

        # Use the potentially modified template URI as the registration key
        final_template_uri = template.uri_template

        # Register the template by directly assigning to the templates dictionary
        self._resource_manager._templates[final_template_uri] = template
        logger.debug(
            f"Registered TEMPLATE: {final_template_uri} ({route.method} {route.path}) with tags: {route.tags}"
        )
