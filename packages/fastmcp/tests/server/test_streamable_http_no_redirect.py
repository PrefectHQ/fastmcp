"""Test that streamable HTTP routes avoid 307 redirects."""

import httpx
import pytest
from starlette.routing import Route

from fastmcp import FastMCP


@pytest.mark.parametrize(
    "server_path",
    ["/mcp", "/mcp/"],
)
def test_streamable_http_route_structure(server_path: str):
    """Test that streamable HTTP routes use Route objects with correct paths."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    # Create HTTP app with specific path
    app = mcp.http_app(transport="http", path=server_path)

    # Find the streamable HTTP route
    streamable_routes = [
        r
        for r in app.routes
        if isinstance(r, Route) and hasattr(r, "path") and r.path == server_path
    ]

    # Verify route exists and uses Route (not Mount)
    assert len(streamable_routes) == 1, (
        f"Should have one streamable route for path {server_path}"
    )
    assert isinstance(streamable_routes[0], Route), "Should use Route, not Mount"
    assert streamable_routes[0].path == server_path, (
        f"Route path should match {server_path}"
    )


async def test_streamable_http_redirect_behavior():
    """Test that non-matching paths get redirected correctly."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    # Create HTTP app with /mcp path (no trailing slash)
    app = mcp.http_app(transport="http", path="/mcp")

    # Test that /mcp/ gets redirected to /mcp
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/mcp/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "http://test/mcp"


async def test_streamable_http_no_mount_routes():
    """Test that streamable HTTP app creates Route objects, not Mount objects."""
    mcp = FastMCP("TestServer")
    app = mcp.http_app(transport="http")

    # Should not find any Mount routes for the streamable HTTP path
    from starlette.routing import Mount

    mount_routes = [
        r
        for r in app.routes
        if isinstance(r, Mount) and hasattr(r, "path") and r.path == "/mcp"
    ]

    assert len(mount_routes) == 0, "Should not have Mount routes for streamable HTTP"

    # Should find Route objects instead
    route_routes = [
        r
        for r in app.routes
        if isinstance(r, Route) and hasattr(r, "path") and r.path == "/mcp"
    ]

    assert len(route_routes) == 1, "Should have exactly one Route for streamable HTTP"
