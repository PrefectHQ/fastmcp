from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from fastmcp.contrib.component_manager.component_service import ComponentService
from fastmcp.exceptions import NotFoundError

from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from typing import TYPE_CHECKING, Any

from fastmcp.server.server import FastMCP

def set_up_component_manager(
    server: FastMCP, root_path: str = "/", required_scopes: list[str] | None = None
):
    """Set up routes for enabling/disabling tools, resources, and prompts.
    Args:
        server: The FastMCP server instance
        required_scopes: Optional list of scopes required for these routes
    Returns:
        A list of routes or mounts for component management
    """

    service = ComponentService(server)
    routes: list[Route] | list[Mount] = []
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
            build_component_manager_enpoints(route_configs, root_path)
        )        
    else:
        if root_path != "/":
            routes.append(
                build_component_manager_enpoints(
                    route_configs, root_path, required_scopes
            ))
        else:
            routes.append(
                build_component_manager_enpoints(
                    {"tool": route_configs["tool"]}, "/tools", required_scopes
            ))
            routes.append(
                build_component_manager_enpoints(
                    {"resource": route_configs["resource"]}, "/resources", required_scopes
            ))
            routes.append(
                build_component_manager_enpoints(
                    {"prompt": route_configs["prompt"]}, "/prompts", required_scopes
            ))

    server._additional_http_routes.extend(routes)


def build_component_manager_enpoints(route_configs, root_path, required_scopes=None) -> list[Route] | Mount:
    component_management_routes: list[Route] = []

    for component in route_configs:
        config: dict[str, Any] = route_configs[component]
        for action in ["enable", "disable"]:

            async def endpoint(
                request: Request,
                action: str = action,
                component: str = component,
                config: dict[str, Any] = config,
            ):
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

            if required_scopes is not None and root_path in ["/tools", "/resources", "/prompts"]:
                path = f"/{{{config['param']}}}/{action}"
            else:
                path = f"/{component}s/{{{config['param']}}}/{action}"

            route = Route(path, endpoint=endpoint, methods=["POST"])
            component_management_routes.append(route)

    if required_scopes is None:
        return component_management_routes
    else:
        return Mount(
            f"{root_path}",
            app=RequireAuthMiddleware(Starlette(routes=component_management_routes),
            required_scopes)
        )