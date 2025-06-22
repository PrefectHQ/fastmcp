"""ATProto MCP Server - Public API exposing Bluesky tools."""

from atproto_mcp import _atproto
from fastmcp import FastMCP

atproto_mcp = FastMCP(
    "ATProto MCP Server",
    dependencies=[
        "atproto_mcp@git+https://github.com/jlowin/fastmcp.git@atproto-example#subdirectory=examples/atproto_mcp",
    ],
)


@atproto_mcp.tool
def atproto_status() -> dict:
    """Check the status of the ATProto connection and current user."""
    return _atproto.get_profile_info()


@atproto_mcp.tool
def post_to_bluesky(text: str) -> dict:
    """Create a new post on Bluesky."""
    return _atproto.create_post(text)


@atproto_mcp.tool
def get_timeline(limit: int = 10) -> dict:
    """Get the authenticated user's timeline."""
    return _atproto.fetch_timeline(limit)


@atproto_mcp.tool
def search_posts(query: str, limit: int = 10) -> dict:
    """Search for posts containing specific text."""
    return _atproto.search_for_posts(query, limit)


@atproto_mcp.tool
def get_notifications(limit: int = 10) -> dict:
    """Get recent notifications for the authenticated user."""
    return _atproto.fetch_notifications(limit)


@atproto_mcp.tool
def follow_user(handle: str) -> dict:
    """Follow a user by their handle."""
    return _atproto.follow_user_by_handle(handle)


@atproto_mcp.tool
def like_post(uri: str) -> dict:
    """Like a post by its AT URI."""
    return _atproto.like_post_by_uri(uri)


@atproto_mcp.tool
def repost(uri: str) -> dict:
    """Repost a post by its AT URI."""
    return _atproto.repost_by_uri(uri)
