"""Type definitions for ATProto MCP server."""

from typing import TypedDict


class ProfileInfo(TypedDict):
    """Profile information response."""

    connected: bool
    handle: str | None
    display_name: str | None
    did: str | None
    followers: int | None
    following: int | None
    posts: int | None
    error: str | None


class PostResult(TypedDict):
    """Result of creating a post."""

    success: bool
    uri: str | None
    cid: str | None
    text: str | None
    created_at: str | None
    error: str | None


class Post(TypedDict):
    """A single post."""

    author: str
    text: str | None
    created_at: str | None
    likes: int
    reposts: int
    replies: int
    uri: str
    cid: str


class TimelineResult(TypedDict):
    """Timeline fetch result."""

    success: bool
    count: int
    posts: list[Post]
    error: str | None


class SearchResult(TypedDict):
    """Search result."""

    success: bool
    query: str
    count: int
    posts: list[Post]
    error: str | None


class Notification(TypedDict):
    """A single notification."""

    reason: str
    author: str | None
    is_read: bool
    indexed_at: str
    uri: str
    cid: str


class NotificationsResult(TypedDict):
    """Notifications fetch result."""

    success: bool
    count: int
    notifications: list[Notification]
    error: str | None


class FollowResult(TypedDict):
    """Result of following a user."""

    success: bool
    handle: str | None
    did: str | None
    uri: str | None
    error: str | None


class LikeResult(TypedDict):
    """Result of liking a post."""

    success: bool
    liked_uri: str | None
    like_uri: str | None
    error: str | None


class RepostResult(TypedDict):
    """Result of reposting."""

    success: bool
    reposted_uri: str | None
    repost_uri: str | None
    error: str | None


class RichTextLink(TypedDict):
    """A link in rich text."""

    text: str
    url: str


class RichTextMention(TypedDict):
    """A mention in rich text."""

    handle: str
    display_text: str | None


class ThreadPost(TypedDict, total=False):
    """A post in a thread."""

    text: str  # Required
    images: list[str] | None
    image_alts: list[str] | None
    links: list[RichTextLink] | None
    mentions: list[RichTextMention] | None
    quote: str | None


class ThreadResult(TypedDict):
    """Result of creating a thread."""

    success: bool
    thread_uri: str | None  # URI of the first post
    post_uris: list[str]
    post_count: int
    error: str | None
