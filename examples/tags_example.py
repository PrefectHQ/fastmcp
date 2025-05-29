"""
Example demonstrating RouteMap tags functionality.

This example shows how to use the tags parameter in RouteMap
to selectively route OpenAPI endpoints based on their tags.
"""

import asyncio

from fastapi import FastAPI

from fastmcp import FastMCP
from fastmcp.server.openapi import MCPType, RouteMap

# Create a FastAPI app with tagged endpoints
app = FastAPI(title="Tagged API Example")


@app.get("/users", tags=["users", "public"])
async def get_users():
    """Get all users - public endpoint"""
    return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]


@app.post("/users", tags=["users", "admin"])
async def create_user(name: str):
    """Create a user - admin only"""
    return {"id": 3, "name": name}


@app.get("/admin/stats", tags=["admin", "internal"])
async def get_admin_stats():
    """Get admin statistics - internal use"""
    return {"total_users": 100, "active_sessions": 25}


@app.get("/health", tags=["public"])
async def health_check():
    """Public health check"""
    return {"status": "healthy"}


@app.get("/metrics")
async def get_metrics():
    """Metrics endpoint with no tags"""
    return {"requests": 1000, "errors": 5}


async def main():
    """Demonstrate different tag-based routing strategies."""

    print("=== Example 1: Make admin-tagged routes tools ===")

    # Strategy 1: Convert admin-tagged routes to tools
    mcp1 = FastMCP.from_fastapi(
        app=app,
        route_maps=[
            RouteMap(methods="*", pattern=r".*", mcp_type=MCPType.TOOL, tags={"admin"}),
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
        ],
    )

    tools = await mcp1.get_tools()
    resources = await mcp1.get_resources()

    print(f"Tools ({len(tools)}): {', '.join(tools.keys())}")
    print(f"Resources ({len(resources)}): {', '.join(resources.keys())}")

    print("\n=== Example 2: Exclude internal routes ===")

    # Strategy 2: Exclude internal routes entirely
    mcp2 = FastMCP.from_fastapi(
        app=app,
        route_maps=[
            RouteMap(
                methods="*", pattern=r".*", mcp_type=MCPType.EXCLUDE, tags={"internal"}
            ),
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
            RouteMap(methods=["POST"], pattern=r".*", mcp_type=MCPType.TOOL),
        ],
    )

    tools = await mcp2.get_tools()
    resources = await mcp2.get_resources()

    print(f"Tools ({len(tools)}): {', '.join(tools.keys())}")
    print(f"Resources ({len(resources)}): {', '.join(resources.keys())}")

    print("\n=== Example 3: Pattern + Tags combination ===")

    # Strategy 3: Routes matching both pattern AND tags
    mcp3 = FastMCP.from_fastapi(
        app=app,
        route_maps=[
            # Admin routes under /admin path -> tools
            RouteMap(
                methods="*",
                pattern=r".*/admin/.*",
                mcp_type=MCPType.TOOL,
                tags={"admin"},
            ),
            # Public routes -> tools
            RouteMap(
                methods="*", pattern=r".*", mcp_type=MCPType.TOOL, tags={"public"}
            ),
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
        ],
    )

    tools = await mcp3.get_tools()
    resources = await mcp3.get_resources()

    print(f"Tools ({len(tools)}): {', '.join(tools.keys())}")
    print(f"Resources ({len(resources)}): {', '.join(resources.keys())}")

    print("\n=== Example 4: Multiple tag AND condition ===")

    # Strategy 4: Routes must have ALL specified tags
    mcp4 = FastMCP.from_fastapi(
        app=app,
        route_maps=[
            # Routes with BOTH "users" AND "admin" tags -> tools
            RouteMap(
                methods="*",
                pattern=r".*",
                mcp_type=MCPType.TOOL,
                tags={"users", "admin"},
            ),
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
        ],
    )

    tools = await mcp4.get_tools()
    resources = await mcp4.get_resources()

    print(f"Tools ({len(tools)}): {', '.join(tools.keys())}")
    print(f"Resources ({len(resources)}): {', '.join(resources.keys())}")


if __name__ == "__main__":
    asyncio.run(main())
