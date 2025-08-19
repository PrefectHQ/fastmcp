"""ATProto MCP Server - Public API exposing Bluesky tools and resources."""

from typing import Annotated

from pydantic import Field

from atproto_mcp import _atproto
from atproto_mcp.settings import settings
from atproto_mcp.types import (
    FollowResult,
    LikeResult,
    NotificationsResult,
    PostResult,
    ProfileInfo,
    RepostResult,
    RichTextLink,
    RichTextMention,
    SearchResult,
    ThreadPost,
    ThreadResult,
    TimelineResult,
)
from fastmcp import FastMCP

atproto_mcp = FastMCP("ATProto MCP Server")


# Resources - read-only operations
@atproto_mcp.resource("atproto://profile/status")
def atproto_status() -> ProfileInfo:
    """Check the status of the ATProto connection and current user profile."""
    return _atproto.get_profile_info()


@atproto_mcp.resource("atproto://timeline")
def get_timeline() -> TimelineResult:
    """Get the authenticated user's timeline feed."""
    return _atproto.fetch_timeline(settings.atproto_timeline_default_limit)


@atproto_mcp.resource("atproto://notifications")
def get_notifications() -> NotificationsResult:
    """Get recent notifications for the authenticated user."""
    return _atproto.fetch_notifications(settings.atproto_notifications_default_limit)


# Tools - actions that modify state
@atproto_mcp.tool
def post(
    text: Annotated[
        str, Field(max_length=300, description="The text content of the post")
    ],
    images: Annotated[
        list[str] | None,
        Field(max_length=4, description="URLs of images to attach (max 4)"),
    ] = None,
    image_alts: Annotated[
        list[str] | None, Field(description="Alt text for each image")
    ] = None,
    links: Annotated[
        list[RichTextLink] | None, Field(description="Links to embed in the text")
    ] = None,
    mentions: Annotated[
        list[RichTextMention] | None, Field(description="User mentions to embed")
    ] = None,
    reply_to: Annotated[
        str | None, Field(description="AT URI of post to reply to")
    ] = None,
    reply_root: Annotated[
        str | None, Field(description="AT URI of thread root (defaults to reply_to)")
    ] = None,
    quote: Annotated[str | None, Field(description="AT URI of post to quote")] = None,
) -> PostResult:
    """Create a post with optional rich features like images, quotes, replies, and rich text.

    Examples:
        - Simple post: post("Hello world!")
        - With image: post("Check this out!", images=["https://example.com/img.jpg"])
        - Reply: post("I agree!", reply_to="at://did/app.bsky.feed.post/123")
        - Quote: post("Great point!", quote="at://did/app.bsky.feed.post/456")
        - Rich text: post("Check out example.com", links=[{"text": "example.com", "url": "https://example.com"}])
    """
    return _atproto.create_post(
        text, images, image_alts, links, mentions, reply_to, reply_root, quote
    )


@atproto_mcp.tool
def follow(
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
def like(
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


@atproto_mcp.tool
def search(
    query: Annotated[str, Field(description="Search query for posts")],
    limit: Annotated[
        int, Field(ge=1, le=100, description="Number of results to return")
    ] = settings.atproto_search_default_limit,
) -> SearchResult:
    """Search for posts containing specific text."""
    return _atproto.search_for_posts(query, limit)


@atproto_mcp.tool
def create_thread(
    posts: Annotated[
        list[ThreadPost],
        Field(
            description="List of posts to create as a thread. Each post can have text, images, links, mentions, and quotes."
        ),
    ],
) -> ThreadResult:
    """Create a thread of posts with automatic linking.

    The first post becomes the root of the thread, and each subsequent post
    replies to the previous one, maintaining the thread structure.

    Example:
        create_thread([
            {"text": "Starting a thread about Python 🧵"},
            {"text": "Python is great for rapid development"},
            {"text": "And the ecosystem is amazing!", "images": ["https://example.com/python.jpg"]}
        ])
    """
    return _atproto.create_thread(posts)
