"""Private ATProto implementation details."""

from datetime import datetime

from atproto import Client

from atproto_mcp.settings import settings
from atproto_mcp.types import (
    FollowResult,
    LikeResult,
    Notification,
    NotificationsResult,
    Post,
    PostResult,
    ProfileInfo,
    RepostResult,
    SearchResult,
    TimelineResult,
)

_client: Client | None = None


def get_client() -> Client:
    """Get or create an authenticated ATProto client."""
    global _client
    if _client is None:
        _client = Client()
        _client.login(settings.atproto_handle, settings.atproto_password)
    return _client


def get_profile_info() -> ProfileInfo:
    """Get profile information for the authenticated user."""
    try:
        client = get_client()
        profile = client.get_profile(client.me.did)
        return ProfileInfo(
            connected=True,
            handle=profile.handle,
            display_name=profile.display_name,
            did=client.me.did,
            followers=profile.followers_count,
            following=profile.follows_count,
            posts=profile.posts_count,
            error=None,
        )
    except Exception as e:
        return ProfileInfo(
            connected=False,
            handle=None,
            display_name=None,
            did=None,
            followers=None,
            following=None,
            posts=None,
            error=str(e),
        )


def create_post(text: str) -> PostResult:
    """Create a new post."""
    try:
        client = get_client()
        post = client.send_post(text=text)
        return PostResult(
            success=True,
            uri=post.uri,
            cid=post.cid,
            text=text,
            created_at=datetime.now().isoformat(),
            error=None,
        )
    except Exception as e:
        return PostResult(
            success=False,
            uri=None,
            cid=None,
            text=None,
            created_at=None,
            error=str(e),
        )


def fetch_timeline(limit: int = 10) -> TimelineResult:
    """Fetch the authenticated user's timeline."""
    try:
        client = get_client()
        timeline = client.get_timeline(limit=limit)

        posts = []
        for feed_view in timeline.feed:
            post = feed_view.post
            posts.append(
                Post(
                    author=post.author.handle,
                    text=post.record.text if hasattr(post.record, "text") else None,
                    created_at=post.record.created_at
                    if hasattr(post.record, "created_at")
                    else None,
                    likes=post.like_count,
                    reposts=post.repost_count,
                    replies=post.reply_count,
                    uri=post.uri,
                )
            )

        return TimelineResult(
            success=True,
            count=len(posts),
            posts=posts,
            error=None,
        )
    except Exception as e:
        return TimelineResult(
            success=False,
            count=0,
            posts=[],
            error=str(e),
        )


def search_for_posts(query: str, limit: int = 10) -> SearchResult:
    """Search for posts containing specific text."""
    try:
        client = get_client()
        search_results = client.app.bsky.feed.search_posts(
            params={"q": query, "limit": limit}
        )

        posts = []
        for post in search_results.posts:
            posts.append(
                Post(
                    author=post.author.handle,
                    text=post.record.text if hasattr(post.record, "text") else None,
                    created_at=post.record.created_at
                    if hasattr(post.record, "created_at")
                    else None,
                    likes=post.like_count,
                    reposts=post.repost_count,
                    replies=post.reply_count,
                    uri=post.uri,
                )
            )

        return SearchResult(
            success=True,
            query=query,
            count=len(posts),
            posts=posts,
            error=None,
        )
    except Exception as e:
        return SearchResult(
            success=False,
            query=query,
            count=0,
            posts=[],
            error=str(e),
        )


def fetch_notifications(limit: int = 10) -> NotificationsResult:
    """Get recent notifications."""
    try:
        client = get_client()
        notifications = client.app.bsky.notification.list_notifications(
            params={"limit": limit}
        )

        notifs = []
        for notif in notifications.notifications:
            notifs.append(
                Notification(
                    reason=notif.reason,
                    author=notif.author.handle if notif.author else None,
                    is_read=notif.is_read,
                    created_at=notif.indexed_at,
                    uri=notif.uri,
                )
            )

        return NotificationsResult(
            success=True,
            count=len(notifs),
            notifications=notifs,
            error=None,
        )
    except Exception as e:
        return NotificationsResult(
            success=False,
            count=0,
            notifications=[],
            error=str(e),
        )


def follow_user_by_handle(handle: str) -> FollowResult:
    """Follow a user by their handle."""
    try:
        client = get_client()
        # Resolve handle to DID
        resolved = client.app.bsky.actor.search_actors(params={"q": handle, "limit": 1})
        if not resolved.actors:
            return FollowResult(
                success=False,
                followed=None,
                did=None,
                uri=None,
                error=f"User {handle} not found",
            )

        user_did = resolved.actors[0].did
        follow = client.follow(user_did)

        return FollowResult(
            success=True,
            followed=handle,
            did=user_did,
            uri=follow.uri,
            error=None,
        )
    except Exception as e:
        return FollowResult(
            success=False,
            followed=None,
            did=None,
            uri=None,
            error=str(e),
        )


def like_post_by_uri(uri: str) -> LikeResult:
    """Like a post by its AT URI."""
    try:
        client = get_client()
        # Parse the URI to get the components
        # URI format: at://did:plc:xxx/app.bsky.feed.post/yyy
        parts = uri.replace("at://", "").split("/")
        if len(parts) != 3 or parts[1] != "app.bsky.feed.post":
            raise ValueError("Invalid post URI format")

        # repo = parts[0]  # Not needed for get_posts
        # rkey = parts[2]  # Not needed for get_posts

        # Get the post to retrieve its CID
        post = client.app.bsky.feed.get_posts(params={"uris": [uri]})
        if not post.posts:
            raise ValueError("Post not found")

        cid = post.posts[0].cid

        # Now like the post with both URI and CID
        like = client.like(uri, cid)
        return LikeResult(
            success=True,
            liked_uri=uri,
            like_uri=like.uri,
            error=None,
        )
    except Exception as e:
        return LikeResult(
            success=False,
            liked_uri=None,
            like_uri=None,
            error=str(e),
        )


def repost_by_uri(uri: str) -> RepostResult:
    """Repost a post by its AT URI."""
    try:
        client = get_client()
        # Parse the URI to get the components
        # URI format: at://did:plc:xxx/app.bsky.feed.post/yyy
        parts = uri.replace("at://", "").split("/")
        if len(parts) != 3 or parts[1] != "app.bsky.feed.post":
            raise ValueError("Invalid post URI format")

        # Get the post to retrieve its CID
        post = client.app.bsky.feed.get_posts(params={"uris": [uri]})
        if not post.posts:
            raise ValueError("Post not found")

        cid = post.posts[0].cid

        # Now repost with both URI and CID
        repost = client.repost(uri, cid)
        return RepostResult(
            success=True,
            reposted_uri=uri,
            repost_uri=repost.uri,
            error=None,
        )
    except Exception as e:
        return RepostResult(
            success=False,
            reposted_uri=None,
            repost_uri=None,
            error=str(e),
        )
