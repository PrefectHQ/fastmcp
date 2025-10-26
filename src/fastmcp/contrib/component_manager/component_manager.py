"""
Routes and helpers for managing tools, resources, and prompts in FastMCP.
Provides endpoints for enabling/disabling components via HTTP, with optional authentication scopes.
"""

from typing import Any

from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from fastmcp.contrib.component_manager.component_service import ComponentService
from fastmcp.exceptions import NotFoundError
from fastmcp.server.server import FastMCP


def set_up_component_manager(
    server: FastMCP, path: str = "/", required_scopes: list[str] | None = None
):
    """Set up routes for enabling/disabling tools, resources, and prompts.
    Args:
        server: The FastMCP server instance
        path: Path used to mount all component-related routes on the server
        required_scopes: Optional list of scopes required for these routes. Applies only if authentication is enabled.
    """

    service = ComponentService(server)
    routes: list[Route] = []
    mounts: list[Mount] = []
    route_configs = {
        "tool": {
            "param": "tool_name",
            "enable": service._enable_tool,
            "disable": service._disable_tool,
        },
        "resource": {
            "param": "uri:path",
            "enable": service._enable_resource,
            "disable": service._disable_resource,
        },
        "prompt": {
            "param": "prompt_name",
            "enable": service._enable_prompt,
            "disable": service._disable_prompt,
        },
    }

    if required_scopes is None:
        routes.extend(build_component_manager_endpoints(route_configs, path))
    else:
        if path != "/":
            mounts.append(
                build_component_manager_mount(route_configs, path, required_scopes)
            )
        else:
            mounts.append(
                build_component_manager_mount(
                    {"tool": route_configs["tool"]}, "/tools", required_scopes
                )
            )
            mounts.append(
                build_component_manager_mount(
                    {"resource": route_configs["resource"]},
                    "/resources",
                    required_scopes,
                )
            )
            mounts.append(
                build_component_manager_mount(
                    {"prompt": route_configs["prompt"]}, "/prompts", required_scopes
                )
            )

    server._additional_http_routes.extend(routes)
    server._additional_http_routes.extend(mounts)


def make_endpoint(action, component, config):
    """
    Factory for creating Starlette endpoint functions for enabling/disabling a component.
    Args:
        action: 'enable' or 'disable'
        component: The component type (e.g., 'tool', 'resource', or 'prompt')
        config: Dict with param and handler functions for the component
    Returns:
        An async endpoint function for Starlette.
    """

    async def endpoint(request: Request):
        name = request.path_params[config["param"].split(":")[0]]

        try:
            await config[action](name)
            return JSONResponse(
                {"message": f"{action.capitalize()}d {component}: {name}"}
            )
        except NotFoundError as e:
            raise StarletteHTTPException(
                status_code=404,
                detail=f"Unknown {component}: {name}",
            ) from e

    return endpoint


def make_route(action, component, config, required_scopes, root_path) -> Route:
    """
    Creates a Starlette Route for enabling/disabling a component.
    Args:
        action: 'enable' or 'disable'
        component: The component type
        config: Dict with param and handler functions
        required_scopes: Optional list of required auth scopes
        root_path: The base path for the route
    Returns:
        A Starlette Route object.
    """
    endpoint = make_endpoint(action, component, config)

    if required_scopes is not None and root_path in [
        "/tools",
        "/resources",
        "/prompts",
    ]:
        path = f"/{{{config['param']}}}/{action}"
    else:
        if root_path != "/" and required_scopes is None:
            path = f"{root_path}/{component}s/{{{config['param']}}}/{action}"
        else:
            path = f"/{component}s/{{{config['param']}}}/{action}"

    return Route(path, endpoint=endpoint, methods=["POST"])


def build_component_manager_endpoints(
    route_configs, root_path, required_scopes=None
) -> list[Route]:
    """
    Build a list of Starlette Route objects for all components/actions.
    Args:
        route_configs: Dict describing component types and their handlers
        root_path: The base path for the routes
        required_scopes: Optional list of required auth scopes
    Returns:
        List of Starlette Route objects for component management.
    """
    component_management_routes: list[Route] = []

    for component in route_configs:
        config: dict[str, Any] = route_configs[component]
        for action in ["enable", "disable"]:
            component_management_routes.append(
                make_route(action, component, config, required_scopes, root_path)
            )

    return component_management_routes


def build_component_manager_mount(route_configs, root_path, required_scopes) -> Mount:
    """
    Build a Starlette Mount with authentication for component management routes.
    Args:
        route_configs: Dict describing component types and their handlers
        root_path: The base path for the mount
        required_scopes: List of required auth scopes
    Returns:
        A Starlette Mount object with authentication middleware.
    """
    component_management_routes: list[Route] = []

    for component in route_configs:
        config: dict[str, Any] = route_configs[component]
        for action in ["enable", "disable"]:
            component_management_routes.append(
                make_route(action, component, config, required_scopes, root_path)
            )

    return Mount(
        f"{root_path}",
        app=RequireAuthMiddleware(
            Starlette(routes=component_management_routes), required_scopes
        ),
    )
