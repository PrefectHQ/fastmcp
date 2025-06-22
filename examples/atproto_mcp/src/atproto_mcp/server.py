"""ATProto MCP Server - Public API exposing Bluesky tools and resources."""

from typing import Annotated

from pydantic import Field

from atproto_mcp import _atproto
from atproto_mcp.types import (
    FollowResult,
    LikeResult,
    NotificationsResult,
    PostResult,
    ProfileInfo,
    RepostResult,
    SearchResult,
    TimelineResult,
)
from fastmcp import FastMCP

atproto_mcp = FastMCP(
    "ATProto MCP Server",
    dependencies=[
        "atproto_mcp@git+https://github.com/jlowin/fastmcp.git@atproto-example#subdirectory=examples/atproto_mcp",
    ],
)


# Resources - read-only operations
@atproto_mcp.resource("atproto://profile/status")
def atproto_status() -> ProfileInfo:
    """Check the status of the ATProto connection and current user profile."""
    return _atproto.get_profile_info()


@atproto_mcp.resource("atproto://timeline")
def get_timeline() -> TimelineResult:
    """Get the authenticated user's timeline feed."""
    return _atproto.fetch_timeline(10)


@atproto_mcp.resource("atproto://search/{query}")
def search_posts(
    query: Annotated[str, Field(description="Search query for posts")],
    limit: Annotated[
        int, Field(default=10, ge=1, le=100, description="Number of results to return")
    ] = 10,
) -> SearchResult:
    """Search for posts containing specific text."""
    return _atproto.search_for_posts(query, limit)


@atproto_mcp.resource("atproto://notifications")
def get_notifications() -> NotificationsResult:
    """Get recent notifications for the authenticated user."""
    return _atproto.fetch_notifications(10)


# Tools - actions that modify state
@atproto_mcp.tool
def post_to_bluesky(
    text: Annotated[
        str, Field(max_length=300, description="The text content of the post")
    ],
) -> PostResult:
    """Create a new post on Bluesky."""
    return _atproto.create_post(text)


@atproto_mcp.tool
def follow_user(
    handle: Annotated[
        str,
        Field(
            description="The handle of the user to follow (e.g., 'user.bsky.social')"
        ),
    ],
) -> FollowResult:
    """Follow a user by their handle."""
    return _atproto.follow_user_by_handle(handle)


@atproto_mcp.tool
def like_post(
    uri: Annotated[str, Field(description="The AT URI of the post to like")],
) -> LikeResult:
    """Like a post by its AT URI."""
    return _atproto.like_post_by_uri(uri)


@atproto_mcp.tool
def repost(
    uri: Annotated[str, Field(description="The AT URI of the post to repost")],
) -> RepostResult:
    """Repost a post by its AT URI."""
    return _atproto.repost_by_uri(uri)
