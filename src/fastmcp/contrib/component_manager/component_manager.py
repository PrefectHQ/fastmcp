from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from fastmcp.contrib.component_manager.component_service import ComponentService
from fastmcp.exceptions import NotFoundError

from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from typing import Any

from fastmcp.server.server import FastMCP

def set_up_component_manager(
    server: FastMCP, path: str = "/", required_scopes: list[str] | None = None
):
    """Set up routes for enabling/disabling tools, resources, and prompts.
    Args:
        server: The FastMCP server instance
        root_path: Path used to mount all component-related routes on the server
        required_scopes: Optional list of scopes required for these routes
    Returns:
        A list of routes or mounts for component management
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
        routes.extend(
            build_component_manager_endpoints(route_configs, path)
        )        
    else:
        if path != "/":
            mounts.append(
                build_component_manager_mount(
                    route_configs, path, required_scopes
            ))
        else:
            mounts.append(
                build_component_manager_mount(
                    {"tool": route_configs["tool"]}, "/tools", required_scopes
            ))
            mounts.append(
                build_component_manager_mount(
                    {"resource": route_configs["resource"]}, "/resources", required_scopes
            ))
            mounts.append(
                build_component_manager_mount(
                    {"prompt": route_configs["prompt"]}, "/prompts", required_scopes
            ))

    server._additional_http_routes.extend(routes)
    server._additional_http_routes.extend(mounts)


def make_endpoint(action, component, config):
    async def endpoint(request: Request):
        name = request.path_params[config["param"].split(":")[0]]

        try:
            await config[action](name)
            return JSONResponse(
                {"message": f"{action.capitalize()}d {component}: {name}"}
            )
        except NotFoundError:
            raise StarletteHTTPException(
                status_code=404,
                detail=f"Unknown {component}: {name}",
            )
    return endpoint

def make_route(action, component, config, required_scopes, root_path) -> Route:
    endpoint = make_endpoint(action, component, config)

    if required_scopes is not None and root_path in ["/tools", "/resources", "/prompts"]:
        path = f"/{{{config['param']}}}/{action}"
    else:
        path = f"/{component}s/{{{config['param']}}}/{action}"

    return Route(path, endpoint=endpoint, methods=["POST"])
    
def build_component_manager_endpoints(route_configs, root_path, required_scopes=None) -> list[Route]:
    component_management_routes: list[Route] = []

    for component in route_configs:
        config: dict[str, Any] = route_configs[component]
        for action in ["enable", "disable"]:
            component_management_routes.append(make_route(action, component, config, required_scopes, root_path))

    return component_management_routes


def build_component_manager_mount(route_configs, root_path, required_scopes) -> Mount:
    component_management_routes: list[Route] = []

    for component in route_configs:
        config: dict[str, Any] = route_configs[component]
        for action in ["enable", "disable"]:
            component_management_routes.append(make_route(action, component, config, required_scopes, root_path))

    return Mount(
        f"{root_path}",
        app=RequireAuthMiddleware(Starlette(routes=component_management_routes), required_scopes)
    )
