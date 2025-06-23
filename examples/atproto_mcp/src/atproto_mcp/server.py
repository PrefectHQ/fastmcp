"""ATProto MCP Server - Public API exposing Bluesky tools and resources."""

from typing import Annotated

from pydantic import Field

from atproto_mcp import _atproto
from atproto_mcp.types import (
    FollowResult,
    ImagePostResult,
    LikeResult,
    NotificationsResult,
    PostResult,
    ProfileInfo,
    QuotePostResult,
    ReplyResult,
    RepostResult,
    RichTextLink,
    RichTextMention,
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


# Advanced tools for richer interactions
@atproto_mcp.tool
def reply_to_post(
    parent_uri: Annotated[str, Field(description="The AT URI of the post to reply to")],
    text: Annotated[str, Field(max_length=300, description="The reply text")],
    root_uri: Annotated[
        str | None, Field(description="The AT URI of the thread root (optional)")
    ] = None,
) -> ReplyResult:
    """Reply to a post, creating a threaded conversation."""
    return _atproto.reply_to_post(parent_uri, text, root_uri)


@atproto_mcp.tool
def post_with_rich_text(
    text: Annotated[
        str,
        Field(
            max_length=300,
            description="The post text with placeholders for links/mentions",
        ),
    ],
    links: Annotated[
        list[RichTextLink] | None, Field(description="Links to embed in the text")
    ] = None,
    mentions: Annotated[
        list[RichTextMention] | None, Field(description="User mentions to embed")
    ] = None,
) -> PostResult:
    """Create a post with rich text formatting including clickable links and mentions."""
    return _atproto.create_post_with_rich_text(text, links, mentions)


@atproto_mcp.tool
def quote_post(
    text: Annotated[
        str, Field(max_length=300, description="Your commentary on the quoted post")
    ],
    quoted_uri: Annotated[str, Field(description="The AT URI of the post to quote")],
) -> QuotePostResult:
    """Create a quote post to share and comment on another post."""
    return _atproto.create_quote_post(text, quoted_uri)


@atproto_mcp.tool
def post_with_images(
    text: Annotated[str, Field(max_length=300, description="The post text")],
    image_urls: Annotated[
        list[str], Field(max_length=4, description="URLs of images to attach (max 4)")
    ],
    alt_texts: Annotated[
        list[str] | None, Field(description="Alt text for each image")
    ] = None,
) -> ImagePostResult:
    """Create a post with images attached."""
    return _atproto.create_post_with_images(text, image_urls, alt_texts)
