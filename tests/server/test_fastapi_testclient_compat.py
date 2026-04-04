from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastmcp import FastMCP


def test_fastapi_testclient_multiple_runs():
    """Test that TestClient can be used multiple times with a mounted FastMCP app.

    This verifies that the StreamableHTTPSessionManager is correctly recreated
    for each lifespan cycle.
    """
    mcp = FastMCP("test")
    mcp_app = mcp.http_app(path="/mcp")

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI):
        # Trigger the sub-app's lifespan
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(lifespan=combined_lifespan)
    app.mount("/analytics", mcp_app)

    # First test run
    with TestClient(app) as client:
        # We use analytics prefix since it's mounted there
        response = client.get("/analytics/mcp")
        # 406 is expected from SSE if Accept header is missing,
        # but the important part is NO RuntimeError
        assert response.status_code in [200, 405, 404, 406]

    # Second test run - this would fail before the fix
    with TestClient(app) as client:
        response = client.get("/analytics/mcp")
        assert response.status_code in [200, 405, 404, 406]


def test_fastapi_testclient_nested_lifespan():
    """Test that TestClient works with custom combined lifespans and multiple iterations."""
    mcp = FastMCP("test")
    mcp_app = mcp.http_app(path="/mcp")

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(lifespan=combined_lifespan)
    app.mount("/analytics", mcp_app)

    # Multiple runs with custom lifespan
    for _ in range(3):
        with TestClient(app) as client:
            response = client.get("/analytics/mcp")
            assert response.status_code in [200, 405, 404, 406]
