"""
HTTP routes for enabling/disabling components in FastMCP.

Provides REST endpoints for controlling component enabled state. The routes
use the authentication of the server whose HTTP app serves them.
"""

from mcp.server.auth.routes import build_resource_metadata_url
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.middleware import RequireAuthMiddleware
from fastmcp.server.server import FastMCP


def set_up_component_manager(
    server: FastMCP, path: str = "/", required_scopes: list[str] | None = None
) -> None:
    """Set up HTTP routes for enabling/disabling tools, resources, and prompts.

    The routes require the same authentication as the MCP endpoint of the
    server whose HTTP app serves them. For a mounted server, that is the
    parent server's auth provider. If the serving server has no auth provider
    and `required_scopes` is omitted, the routes are unauthenticated, like the
    MCP endpoint itself.

    Args:
        server: The FastMCP server instance.
        path: Base path for component management routes.
        required_scopes: Scopes a token must have to use these routes, in
            addition to the scopes the server's auth provider requires. An
            empty list requires authentication without extra scopes. Omit it
            to require exactly what the MCP endpoint requires. If the serving
            server has no auth provider, routes with `required_scopes` reject
            every request with 401.

    Routes created:
        POST /tools/{name}/enable[?version=v1]
        POST /tools/{name}/disable[?version=v1]
        POST /resources/{uri}/enable[?version=v1]
        POST /resources/{uri}/disable[?version=v1]
        POST /prompts/{name}/enable[?version=v1]
        POST /prompts/{name}/disable[?version=v1]
    """
    routes = _build_routes(server, path, required_scopes)
    server._additional_http_routes.extend(routes)


class _ComponentManagerAuth:
    """Apply the serving server's authentication to a management route.

    The auth provider is resolved for each request from the HTTP app that
    serves it, so routes forwarded from a mounted server use the parent's
    provider, and a provider assigned after setup still applies.
    """

    def __init__(
        self, app: ASGIApp, server: FastMCP, required_scopes: list[str] | None
    ) -> None:
        self.app = app
        self.server = server
        self.required_scopes = required_scopes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        auth, mcp_path = _serving_auth(scope, self.server)
        if auth is None and self.required_scopes is None:
            await self.app(scope, receive, send)
            return

        scopes = list(auth.required_scopes) if auth is not None else []
        for scope_name in self.required_scopes or []:
            if scope_name not in scopes:
                scopes.append(scope_name)
        challenge_scopes = (
            auth.get_challenge_scopes(scopes) if auth is not None else None
        )
        resource_url = (
            auth._get_resource_url(mcp_path)
            if auth is not None and mcp_path is not None
            else None
        )
        resource_metadata_url = (
            build_resource_metadata_url(resource_url) if resource_url else None
        )
        guarded = RequireAuthMiddleware(
            self.app,
            scopes,
            resource_metadata_url=resource_metadata_url,
            challenge_scopes=challenge_scopes,
        )
        await guarded(scope, receive, send)


def _serving_auth(
    scope: Scope, server: FastMCP
) -> tuple[AuthProvider | None, str | None]:
    """Return the auth provider and MCP path of the HTTP app serving this request.

    FastMCP's HTTP app factories store the provider they were built with, and
    the path of the MCP endpoint, on the app state. Routes served by any other
    app use `server.auth` and have no known MCP path.
    """
    app = scope.get("app")
    if isinstance(app, Starlette) and isinstance(
        getattr(app.state, "fastmcp_server", None), FastMCP
    ):
        auth = getattr(app.state, "fastmcp_auth", None)
        mcp_path = getattr(app.state, "path", None)
        return (
            auth if isinstance(auth, AuthProvider) else None,
            mcp_path if isinstance(mcp_path, str) else None,
        )
    return server.auth, None


def _build_routes(
    server: FastMCP, base_path: str, required_scopes: list[str] | None
) -> list[Route]:
    """Build all component management routes."""
    prefix = base_path.rstrip("/") if base_path != "/" else ""
    middleware = [
        Middleware(
            _ComponentManagerAuth, server=server, required_scopes=required_scopes
        )
    ]

    def route(route_path: str, component_type: str, action: str) -> Route:
        return Route(
            f"{prefix}{route_path}",
            endpoint=_make_endpoint(server, component_type, action),
            methods=["POST"],
            middleware=middleware,
        )

    return [
        route("/tools/{name}/enable", "tool", "enable"),
        route("/tools/{name}/disable", "tool", "disable"),
        route("/resources/{uri:path}/enable", "resource", "enable"),
        route("/resources/{uri:path}/disable", "resource", "disable"),
        route("/prompts/{name}/enable", "prompt", "enable"),
        route("/prompts/{name}/disable", "prompt", "disable"),
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
