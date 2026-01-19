"""Example: Expose resources as tools using ResourcesAsTools transform.

This example shows how to use ResourcesAsTools to make resources accessible
to clients that only support tools (not the resources protocol).

Run with:
    uv run python examples/resources_as_tools/server.py
"""

from fastmcp import FastMCP
from fastmcp.server.transforms import ResourcesAsTools

mcp = FastMCP("Resource Tools Demo")


# Static resource - has a fixed URI
@mcp.resource("config://app")
def app_config() -> str:
    """Application configuration."""
    return """
    {
        "app_name": "My App",
        "version": "1.0.0",
        "debug": false
    }
    """


# Another static resource
@mcp.resource("readme://main")
def readme() -> str:
    """Project README."""
    return """
    # My Project

    This is an example project demonstrating ResourcesAsTools.
    """


# Resource template - URI has placeholders
@mcp.resource("user://{user_id}/profile")
def user_profile(user_id: str) -> str:
    """Get a user's profile by ID."""
    return f"""
    {{
        "user_id": "{user_id}",
        "name": "User {user_id}",
        "email": "user{user_id}@example.com"
    }}
    """


# Another template with multiple parameters
@mcp.resource("file://{directory}/{filename}")
def read_file(directory: str, filename: str) -> str:
    """Read a file from a directory."""
    return f"Contents of {directory}/{filename}"


# Add the transform - this creates list_resources and read_resource tools
mcp.add_transform(ResourcesAsTools(mcp))


if __name__ == "__main__":
    mcp.run()
