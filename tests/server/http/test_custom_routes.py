import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp import FastMCP
from fastmcp.server.http import create_sse_app, create_streamable_http_app


class TestCustomRoutes:
    @pytest.fixture
    def server_with_custom_route(self):
        """Create a FastMCP server with a custom route."""
        server = FastMCP()

        @server.custom_route("/custom-route", methods=["GET"])
        async def custom_route(request: Request):
            return JSONResponse({"message": "custom route"})

        return server

    def test_custom_routes_apply_filtering_http_app(self, server_with_custom_route):
        """Test that custom routes are included when using server.http_app()."""
        # Get the app via server.http_app()
        app = server_with_custom_route.http_app()

        # Verify that the custom route is included
        custom_route_found = False
        for route in app.routes:
            if isinstance(route, Route) and route.path == "/custom-route":
                custom_route_found = True
                break

        assert custom_route_found, "Custom route was not found in app routes"

    def test_custom_routes_via_streamable_http_app_direct(
        self, server_with_custom_route
    ):
        """Test that custom routes are included when using create_streamable_http_app directly."""
        # Create the app by calling the constructor function directly
        app = create_streamable_http_app(
            server=server_with_custom_route, streamable_http_path="/api"
        )

        # Verify that the custom route is included
        custom_route_found = False
        for route in app.routes:
            if isinstance(route, Route) and route.path == "/custom-route":
                custom_route_found = True
                break

        assert custom_route_found, "Custom route was not found in app routes"

    def test_custom_routes_via_sse_app_direct(self, server_with_custom_route):
        """Test that custom routes are included when using create_sse_app directly."""
        # Create the app by calling the constructor function directly
        app = create_sse_app(
            server=server_with_custom_route, message_path="/message", sse_path="/sse/"
        )

        # Verify that the custom route is included
        custom_route_found = False
        for route in app.routes:
            if isinstance(route, Route) and route.path == "/custom-route":
                custom_route_found = True
                break

        assert custom_route_found, "Custom route was not found in app routes"

    def test_multiple_custom_routes(
        self,
    ):
        """Test that multiple custom routes are included in both methods."""
        server = FastMCP()

        custom_paths = ["/route1", "/route2", "/route3"]

        # Add multiple custom routes
        for path in custom_paths:

            @server.custom_route(path, methods=["GET"])
            async def custom_route(request: Request):
                return JSONResponse({"message": f"route {path}"})

        # Test with server.http_app()
        app1 = server.http_app()

        # Test with direct constructor call
        app2 = create_streamable_http_app(server=server, streamable_http_path="/api")

        # Check all routes are in both apps
        for path in custom_paths:
            # Check in app1
            route_in_app1 = any(
                isinstance(route, Route) and route.path == path for route in app1.routes
            )
            assert route_in_app1, f"Route {path} not found in server.http_app()"

            # Check in app2
            route_in_app2 = any(
                isinstance(route, Route) and route.path == path for route in app2.routes
            )
            assert route_in_app2, (
                f"Route {path} not found in create_streamable_http_app()"
            )
