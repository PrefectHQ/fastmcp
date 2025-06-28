"""Social actions like follow, like, and repost."""

from atproto_mcp.types import FollowResult, LikeResult, RepostResult

from ._client import get_client


def follow_user_by_handle(handle: str) -> FollowResult:
    """Follow a user by their handle."""
    try:
        client = get_client()
        # Search for the user to get their DID
        results = client.app.bsky.actor.search_actors(params={"q": handle, "limit": 1})
        if not results.actors:
            return FollowResult(
                success=False,
                did=None,
                handle=None,
                uri=None,
                error=f"User @{handle} not found",
            )

        actor = results.actors[0]
        # Create the follow
        follow = client.follow(actor.did)
        return FollowResult(
            success=True,
            did=actor.did,
            handle=actor.handle,
            uri=follow.uri,
            error=None,
        )
    except Exception as e:
        return FollowResult(
            success=False,
            did=None,
            handle=None,
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
