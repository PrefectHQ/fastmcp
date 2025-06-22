from datetime import datetime

from atproto import Client

from atproto_mcp.settings import settings
from fastmcp import FastMCP

atproto_mcp = FastMCP(
    "ATProto MCP Server",
    dependencies=[
        "atproto_mcp@git+https://github.com/jlowin/fastmcp.git@atproto-example#subdirectory=examples/atproto_mcp",
    ],
)

_client: Client | None = None


def get_client() -> Client:
    """Get or create an authenticated ATProto client."""
    global _client
    if _client is None:
        _client = Client()
        _client.login(settings.atproto_handle, settings.atproto_password)
    return _client


@atproto_mcp.tool
def atproto_status() -> dict:
    """Check the status of the ATProto connection and current user."""
    try:
        client = get_client()
        profile = client.get_profile(client.me.did)
        return {
            "connected": True,
            "handle": profile.handle,
            "display_name": profile.display_name,
            "did": client.me.did,
            "followers": profile.followers_count,
            "following": profile.follows_count,
            "posts": profile.posts_count,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


@atproto_mcp.tool
def post_to_bluesky(text: str) -> dict:
    """Create a new post on Bluesky."""
    try:
        client = get_client()
        post = client.send_post(text=text)
        return {
            "success": True,
            "uri": post.uri,
            "cid": post.cid,
            "text": text,
            "created_at": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@atproto_mcp.tool
def get_timeline(limit: int = 10) -> dict:
    """Get the authenticated user's timeline."""
    try:
        client = get_client()
        timeline = client.get_timeline(limit=limit)

        posts = []
        for feed_view in timeline.feed:
            post = feed_view.post
            posts.append(
                {
                    "author": post.author.handle,
                    "text": post.record.text if hasattr(post.record, "text") else None,
                    "created_at": post.record.created_at
                    if hasattr(post.record, "created_at")
                    else None,
                    "likes": post.like_count,
                    "reposts": post.repost_count,
                    "replies": post.reply_count,
                    "uri": post.uri,
                }
            )

        return {
            "success": True,
            "count": len(posts),
            "posts": posts,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@atproto_mcp.tool
def search_posts(query: str, limit: int = 10) -> dict:
    """Search for posts containing specific text."""
    try:
        client = get_client()
        search_results = client.app.bsky.feed.search_posts(
            params={"q": query, "limit": limit}
        )

        posts = []
        for post in search_results.posts:
            posts.append(
                {
                    "author": post.author.handle,
                    "text": post.record.text if hasattr(post.record, "text") else None,
                    "created_at": post.record.created_at
                    if hasattr(post.record, "created_at")
                    else None,
                    "likes": post.like_count,
                    "reposts": post.repost_count,
                    "replies": post.reply_count,
                    "uri": post.uri,
                }
            )

        return {
            "success": True,
            "query": query,
            "count": len(posts),
            "posts": posts,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@atproto_mcp.tool
def get_notifications(limit: int = 10) -> dict:
    """Get recent notifications for the authenticated user."""
    try:
        client = get_client()
        notifications = client.app.bsky.notification.list_notifications(
            params={"limit": limit}
        )

        notifs = []
        for notif in notifications.notifications:
            notifs.append(
                {
                    "reason": notif.reason,
                    "author": notif.author.handle if notif.author else None,
                    "is_read": notif.is_read,
                    "created_at": notif.indexed_at,
                    "uri": notif.uri,
                }
            )

        return {
            "success": True,
            "count": len(notifs),
            "notifications": notifs,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@atproto_mcp.tool
def follow_user(handle: str) -> dict:
    """Follow a user by their handle."""
    try:
        client = get_client()
        # Resolve handle to DID
        resolved = client.app.bsky.actor.search_actors(q=handle, limit=1)
        if not resolved.actors:
            return {"success": False, "error": f"User {handle} not found"}

        user_did = resolved.actors[0].did
        follow = client.follow(user_did)

        return {
            "success": True,
            "followed": handle,
            "did": user_did,
            "uri": follow.uri,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@atproto_mcp.tool
def like_post(uri: str) -> dict:
    """Like a post by its AT URI."""
    try:
        client = get_client()
        like = client.like(uri)
        return {
            "success": True,
            "liked_uri": uri,
            "like_uri": like.uri,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@atproto_mcp.tool
def repost(uri: str) -> dict:
    """Repost a post by its AT URI."""
    try:
        client = get_client()
        repost = client.repost(uri)
        return {
            "success": True,
            "reposted_uri": uri,
            "repost_uri": repost.uri,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
