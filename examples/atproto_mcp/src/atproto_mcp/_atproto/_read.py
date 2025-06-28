"""Read-only operations for timeline, search, and notifications."""

from atproto_mcp.types import (
    Notification,
    NotificationsResult,
    Post,
    SearchResult,
    TimelineResult,
)

from ._client import get_client


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
                    uri=post.uri,
                    cid=post.cid,
                    text=post.record.text if hasattr(post.record, "text") else "",
                    author=post.author.handle,
                    created_at=post.record.created_at,
                    likes=post.like_count or 0,
                    reposts=post.repost_count or 0,
                    replies=post.reply_count or 0,
                )
            )

        return TimelineResult(
            success=True,
            posts=posts,
            count=len(posts),
            error=None,
        )
    except Exception as e:
        return TimelineResult(
            success=False,
            posts=[],
            count=0,
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
                    uri=post.uri,
                    cid=post.cid,
                    text=post.record.text if hasattr(post.record, "text") else "",
                    author=post.author.handle,
                    created_at=post.record.created_at,
                    likes=post.like_count or 0,
                    reposts=post.repost_count or 0,
                    replies=post.reply_count or 0,
                )
            )

        return SearchResult(
            success=True,
            query=query,
            posts=posts,
            count=len(posts),
            error=None,
        )
    except Exception as e:
        return SearchResult(
            success=False,
            query=query,
            posts=[],
            count=0,
            error=str(e),
        )


def fetch_notifications(limit: int = 10) -> NotificationsResult:
    """Fetch recent notifications."""
    try:
        client = get_client()
        notifs = client.app.bsky.notification.list_notifications(
            params={"limit": limit}
        )

        notifications = []
        for notif in notifs.notifications:
            notifications.append(
                Notification(
                    uri=notif.uri,
                    cid=notif.cid,
                    author=notif.author.handle,
                    reason=notif.reason,
                    is_read=notif.is_read,
                    indexed_at=notif.indexed_at,
                )
            )

        return NotificationsResult(
            success=True,
            notifications=notifications,
            count=len(notifications),
            error=None,
        )
    except Exception as e:
        return NotificationsResult(
            success=False,
            notifications=[],
            count=0,
            error=str(e),
        )
