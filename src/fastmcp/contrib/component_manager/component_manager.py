"""
HTTP routes for enabling/disabling components in FastMCP.

Provides REST endpoints for controlling component enabled state with optional
authentication scopes.
"""

from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from fastmcp.server.server import FastMCP


def set_up_component_manager(
    server: FastMCP, path: str = "/", required_scopes: list[str] | None = None
) -> None:
    """Set up HTTP routes for enabling/disabling tools, resources, and prompts.

    Args:
        server: The FastMCP server instance.
        path: Base path for component management routes.
        required_scopes: Optional list of scopes required for these routes.
            Applies only if authentication is enabled.

    Routes created:
        POST /tools/{name}/enable[?version=v1]
        POST /tools/{name}/disable[?version=v1]
        POST /resources/{uri}/enable[?version=v1]
        POST /resources/{uri}/disable[?version=v1]
        POST /prompts/{name}/enable[?version=v1]
        POST /prompts/{name}/disable[?version=v1]
    """
    if required_scopes is None:
        # No auth - include path prefix in routes
        routes = _build_routes(server, path)
        server._additional_http_routes.extend(routes)
    else:
        # With auth - Mount handles path prefix, routes shouldn't have it
        routes = _build_routes(server, "/")
        mount = Mount(
            path if path != "/" else "",
            app=RequireAuthMiddleware(Starlette(routes=routes), required_scopes),
        )
        server._additional_http_routes.append(mount)


def _build_routes(server: FastMCP, base_path: str) -> list[Route]:
    """Build all component management routes."""
    prefix = base_path.rstrip("/") if base_path != "/" else ""

    return [
        # Tools
        Route(
            f"{prefix}/tools/{{name}}/enable",
            endpoint=_make_endpoint(server, "tool", "enable"),
            methods=["POST"],
        ),
        Route(
            f"{prefix}/tools/{{name}}/disable",
            endpoint=_make_endpoint(server, "tool", "disable"),
            methods=["POST"],
        ),
        # Resources
        Route(
            f"{prefix}/resources/{{uri:path}}/enable",
            endpoint=_make_endpoint(server, "resource", "enable"),
            methods=["POST"],
        ),
        Route(
            f"{prefix}/resources/{{uri:path}}/disable",
            endpoint=_make_endpoint(server, "resource", "disable"),
            methods=["POST"],
        ),
        # Prompts
        Route(
            f"{prefix}/prompts/{{name}}/enable",
            endpoint=_make_endpoint(server, "prompt", "enable"),
            methods=["POST"],
        ),
        Route(
            f"{prefix}/prompts/{{name}}/disable",
            endpoint=_make_endpoint(server, "prompt", "disable"),
            methods=["POST"],
        ),
    ]


def _make_endpoint(server: FastMCP, component_type: str, action: str):
    """Create an endpoint function for enabling/disabling a component type."""

    async def endpoint(request: Request) -> JSONResponse:
        # Get name from path params (tools/prompts use 'name', resources use 'uri')
        name = request.path_params.get("name") or request.path_params.get("uri")
        version = request.query_params.get("version")

        # Map component type to components list
        # Note: "resource" in the route can refer to either a resource or template
        # We need to check if it's a template (contains {}) and use "template" if so
        if component_type == "resource" and name is not None and "{" in name:
            components = ["template"]
        elif component_type == "resource":
            components = ["resource"]
        else:
            component_map = {
                "tool": ["tool"],
                "prompt": ["prompt"],
            }
            components = component_map[component_type]

        # Call server.enable() or server.disable()
        method = getattr(server, action)
        method(names={name} if name else None, version=version, components=components)

        return JSONResponse(
            {"message": f"{action.capitalize()}d {component_type}: {name}"}
        )

    return endpoint
