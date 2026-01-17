"""OpenAPIProvider for creating MCP components from OpenAPI specifications."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any, Literal

import httpx
from jsonschema_path import SchemaPath

from fastmcp.prompts import Prompt
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.openapi.components import (
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
    _slugify,
)
from fastmcp.server.providers.openapi.routing import (
    DEFAULT_ROUTE_MAPPINGS,
    ComponentFn,
    MCPType,
    RouteMap,
    RouteMapFn,
    _determine_route_type,
)
from fastmcp.tools.tool import Tool
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.openapi import (
    HTTPRoute,
    extract_output_schema_from_responses,
    format_simple_description,
    parse_openapi_to_http_routes,
)
from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.versions import VersionSpec, version_sort_key

__all__ = [
    "OpenAPIProvider",
]

logger = get_logger(__name__)


class OpenAPIProvider(Provider):
    """Provider that creates MCP components from an OpenAPI specification.

    Components are created eagerly during initialization by parsing the OpenAPI
    spec. Each component makes HTTP calls to the described API endpoints.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.providers.openapi import OpenAPIProvider
        import httpx

        client = httpx.AsyncClient(base_url="https://api.example.com")
        provider = OpenAPIProvider(openapi_spec=spec, client=client)

        mcp = FastMCP("API Server")
        mcp.add_provider(provider)
        ```
    """

    def __init__(
        self,
        openapi_spec: dict[str, Any],
        client: httpx.AsyncClient,
        *,
        route_maps: list[RouteMap] | None = None,
        route_map_fn: RouteMapFn | None = None,
        mcp_component_fn: ComponentFn | None = None,
        mcp_names: dict[str, str] | None = None,
        tags: set[str] | None = None,
        timeout: float | None = None,
    ):
        """Initialize provider by parsing OpenAPI spec and creating components.

        Args:
            openapi_spec: OpenAPI schema as a dictionary
            client: httpx AsyncClient for making HTTP requests
            route_maps: Optional list of RouteMap objects defining route mappings
            route_map_fn: Optional callable for advanced route type mapping
            mcp_component_fn: Optional callable for component customization
            mcp_names: Optional dictionary mapping operationId to component names
            tags: Optional set of tags to add to all components
            timeout: Optional timeout (in seconds) for all requests
        """
        super().__init__()

        self._client = client
        self._timeout = timeout
        self._mcp_component_fn = mcp_component_fn

        # Keep track of names to detect collisions
        self._used_names: dict[str, Counter[str]] = {
            "tool": Counter(),
            "resource": Counter(),
            "resource_template": Counter(),
            "prompt": Counter(),
        }

        # Pre-created component storage
        self._tools: dict[str, OpenAPITool] = {}
        self._resources: dict[str, OpenAPIResource] = {}
        self._templates: dict[str, OpenAPIResourceTemplate] = {}

        # Create openapi-core Spec and RequestDirector
        try:
            self._spec = SchemaPath.from_dict(openapi_spec)  # type: ignore[arg-type]
            self._director = RequestDirector(self._spec)
        except Exception as e:
            logger.exception("Failed to initialize RequestDirector")
            raise ValueError(f"Invalid OpenAPI specification: {e}") from e

        http_routes = parse_openapi_to_http_routes(openapi_spec)

        # Process routes
        route_maps = (route_maps or []) + DEFAULT_ROUTE_MAPPINGS
        for route in http_routes:
            route_map = _determine_route_type(route, route_maps)
            route_type = route_map.mcp_type

            if route_map_fn is not None:
                try:
                    result = route_map_fn(route, route_type)
                    if result is not None:
                        route_type = result
                        logger.debug(
                            f"Route {route.method} {route.path} mapping customized: "
                            f"type={route_type.name}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Error in route_map_fn for {route.method} {route.path}: {e}. "
                        f"Using default values."
                    )

            component_name = self._generate_default_name(route, mcp_names)
            route_tags = set(route.tags) | route_map.mcp_tags | (tags or set())

            if route_type == MCPType.TOOL:
                self._create_openapi_tool(route, component_name, tags=route_tags)
            elif route_type == MCPType.RESOURCE:
                self._create_openapi_resource(route, component_name, tags=route_tags)
            elif route_type == MCPType.RESOURCE_TEMPLATE:
                self._create_openapi_template(route, component_name, tags=route_tags)
            elif route_type == MCPType.EXCLUDE:
                logger.debug(f"Excluding route: {route.method} {route.path}")

        logger.debug(f"Created OpenAPIProvider with {len(http_routes)} routes")

    def _generate_default_name(
        self, route: HTTPRoute, mcp_names_map: dict[str, str] | None = None
    ) -> str:
        """Generate a default name from the route."""
        mcp_names_map = mcp_names_map or {}

        if route.operation_id:
            if route.operation_id in mcp_names_map:
                name = mcp_names_map[route.operation_id]
            else:
                name = route.operation_id.split("__")[0]
        else:
            name = route.summary or f"{route.method}_{route.path}"

        name = _slugify(name)

        if len(name) > 56:
            name = name[:56]

        return name

    def _get_unique_name(
        self,
        name: str,
        component_type: Literal["tool", "resource", "resource_template", "prompt"],
    ) -> str:
        """Ensure the name is unique by appending numbers if needed."""
        self._used_names[component_type][name] += 1
        if self._used_names[component_type][name] == 1:
            return name

        new_name = f"{name}_{self._used_names[component_type][name]}"
        logger.debug(
            f"Name collision: '{name}' exists as {component_type}. Using '{new_name}'."
        )
        return new_name

    def _create_openapi_tool(
        self,
        route: HTTPRoute,
        name: str,
        tags: set[str],
    ) -> None:
        """Create and register an OpenAPITool."""
        combined_schema = route.flat_param_schema
        output_schema = extract_output_schema_from_responses(
            route.responses,
            route.response_schemas,
            route.openapi_version,
        )

        tool_name = self._get_unique_name(name, "tool")
        base_description = (
            route.description
            or route.summary
            or f"Executes {route.method} {route.path}"
        )
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

        if self._mcp_component_fn is not None:
            try:
                self._mcp_component_fn(route, tool)
                logger.debug(f"Tool {tool_name} customized by component_fn")
            except Exception as e:
                logger.warning(f"Error in component_fn for tool {tool_name}: {e}")

        self._tools[tool.name] = tool

    def _create_openapi_resource(
        self,
        route: HTTPRoute,
        name: str,
        tags: set[str],
    ) -> None:
        """Create and register an OpenAPIResource."""
        resource_name = self._get_unique_name(name, "resource")
        resource_uri = f"resource://{resource_name}"
        base_description = (
            route.description or route.summary or f"Represents {route.path}"
        )
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

        if self._mcp_component_fn is not None:
            try:
                self._mcp_component_fn(route, resource)
                logger.debug(f"Resource {resource_uri} customized by component_fn")
            except Exception as e:
                logger.warning(
                    f"Error in component_fn for resource {resource_uri}: {e}"
                )

        self._resources[str(resource.uri)] = resource

    def _create_openapi_template(
        self,
        route: HTTPRoute,
        name: str,
        tags: set[str],
    ) -> None:
        """Create and register an OpenAPIResourceTemplate."""
        template_name = self._get_unique_name(name, "resource_template")

        path_params = sorted(p.name for p in route.parameters if p.location == "path")
        uri_template_str = f"resource://{template_name}"
        if path_params:
            uri_template_str += "/" + "/".join(f"{{{p}}}" for p in path_params)

        base_description = (
            route.description or route.summary or f"Template for {route.path}"
        )
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

        if self._mcp_component_fn is not None:
            try:
                self._mcp_component_fn(route, template)
                logger.debug(f"Template {uri_template_str} customized by component_fn")
            except Exception as e:
                logger.warning(
                    f"Error in component_fn for template {uri_template_str}: {e}"
                )

        self._templates[template.uri_template] = template

    # -------------------------------------------------------------------------
    # Provider interface
    # -------------------------------------------------------------------------

    async def _list_tools(self) -> Sequence[Tool]:
        """Return all tools created from the OpenAPI spec."""
        return list(self._tools.values())

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name."""
        tool = self._tools.get(name)
        if tool is None:
            return None
        if version is not None and not version.matches(tool.version):
            return None
        return tool

    async def _list_resources(self) -> Sequence[Resource]:
        """Return all resources created from the OpenAPI spec."""
        return list(self._resources.values())

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get a resource by URI."""
        resource = self._resources.get(uri)
        if resource is None:
            return None
        if version is not None and not version.matches(resource.version):
            return None
        return resource

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """Return all resource templates created from the OpenAPI spec."""
        return list(self._templates.values())

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get a resource template that matches the given URI."""
        matching = [t for t in self._templates.values() if t.matches(uri) is not None]
        if not matching:
            return None
        if version is not None:
            matching = [t for t in matching if version.matches(t.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)  # type: ignore[type-var]

    async def _list_prompts(self) -> Sequence[Prompt]:
        """Return empty list - OpenAPI doesn't create prompts."""
        return []

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Return empty list - OpenAPI components don't support tasks."""
        return []
