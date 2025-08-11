"""FastMCP server implementation for OpenAPI integration."""

import re
from collections import Counter
from typing import Any, Literal

import httpx
from jsonschema_path import SchemaPath

# Import from our new utilities and components
from fastmcp.experimental.utilities.openapi import (
    HTTPRoute,
    extract_output_schema_from_responses,
    format_simple_description,
    parse_openapi_to_http_routes,
)
from fastmcp.experimental.utilities.openapi.director import RequestDirector
from fastmcp.server.server import FastMCP
from fastmcp.utilities.logging import get_logger

from .components import (
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
)
from .routing import (
    DEFAULT_ROUTE_MAPPINGS,
    ComponentFn,
    MCPType,
    RouteMap,
    RouteMapFn,
    _determine_route_type,
)

logger = get_logger(__name__)


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

        # Create openapi-core Spec and RequestDirector for stateless request building
        try:
            self._spec = SchemaPath.from_dict(openapi_spec)  # type: ignore[arg-type]
            self._director = RequestDirector(self._spec)
        except Exception as e:
            logger.error(f"Failed to initialize RequestDirector: {e}")
            raise ValueError(f"Invalid OpenAPI specification: {e}") from e

        http_routes = parse_openapi_to_http_routes(openapi_spec)

        # Process routes
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

            # Create components using simplified approach with RequestDirector
            if route_type == MCPType.TOOL:
                self._create_openapi_tool(route, component_name, tags=route_tags)
            elif route_type == MCPType.RESOURCE:
                self._create_openapi_resource(route, component_name, tags=route_tags)
            elif route_type == MCPType.RESOURCE_TEMPLATE:
                self._create_openapi_template(route, component_name, tags=route_tags)
            elif route_type == MCPType.EXCLUDE:
                logger.debug(f"Excluding route: {route.method} {route.path}")

        logger.debug(f"Created FastMCP OpenAPI server with {len(http_routes)} routes")

    def _generate_default_name(
        self, route: HTTPRoute, mcp_names_map: dict[str, str] | None = None
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
        route: HTTPRoute,
        name: str,
        tags: set[str],
    ):
        """Creates and registers an OpenAPITool with enhanced description."""
        # Use pre-calculated schema from route
        combined_schema = route.flat_param_schema

        # Extract output schema from OpenAPI responses
        output_schema = extract_output_schema_from_responses(
            route.responses,
            route.response_schemas,
            route.openapi_version,
        )

        # Get a unique tool name
        tool_name = self._get_unique_name(name, "tool")

        base_description = (
            route.description
            or route.summary
            or f"Executes {route.method} {route.path}"
        )

        # Use simplified description formatter for tools
        enhanced_description = format_simple_description(
            base_description=base_description,
            parameters=route.parameters,
            request_body=route.request_body,
        )

        tool = OpenAPITool(
            client=self._client,
            route=route,
            director=self._director,
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

    def _create_openapi_resource(
        self,
        route: HTTPRoute,
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

        # Use simplified description for resources
        enhanced_description = format_simple_description(
            base_description=base_description,
            parameters=route.parameters,
            request_body=route.request_body,
        )

        resource = OpenAPIResource(
            client=self._client,
            route=route,
            director=self._director,
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

    def _create_openapi_template(
        self,
        route: HTTPRoute,
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

        # Use simplified description for resource templates
        enhanced_description = format_simple_description(
            base_description=base_description,
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
            director=self._director,
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


# Export public symbols
__all__ = [
    "FastMCPOpenAPI",
]
