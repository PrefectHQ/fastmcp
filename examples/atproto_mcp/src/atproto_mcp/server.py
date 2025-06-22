from atproto_mcp.settings import settings
from fastmcp import FastMCP

atproto_mcp = FastMCP(
    "ATProto MCP Server",
    dependencies=[
        "atproto@git+https://github.com/MarshalX/atproto.git@refs/pull/605/head",
        "pydantic-settings>=2.0.0",
        "websockets>=15.0.1",
    ],
)


@atproto_mcp.tool
def atproto_status() -> str:
    """Checks the status of the ATProto connection."""
    try:
        # For now, just verify settings are loaded
        if settings.atproto_handle and settings.atproto_password:
            return (
                f"ATProto credentials configured for handle: {settings.atproto_handle}"
            )
        else:
            return "ATProto credentials not configured"
    except Exception as e:
        return f"ATProto status check failed: {e}"
