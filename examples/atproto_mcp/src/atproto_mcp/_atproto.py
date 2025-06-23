"""Private ATProto implementation details."""

from datetime import datetime

from atproto import Client, models

from atproto_mcp.settings import settings
from atproto_mcp.types import (
    FollowResult,
    ImagePostResult,
    LikeResult,
    Notification,
    NotificationsResult,
    Post,
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


def reply_to_post(
    parent_uri: str, text: str, root_uri: str | None = None
) -> ReplyResult:
    """Reply to a post."""
    try:
        client = get_client()

        # Get parent post to extract CID
        parent_post = client.app.bsky.feed.get_posts(params={"uris": [parent_uri]})
        if not parent_post.posts:
            raise ValueError("Parent post not found")

        parent_cid = parent_post.posts[0].cid
        # Create a proper StrongRef object
        parent_ref = models.ComAtprotoRepoStrongRef.Main(uri=parent_uri, cid=parent_cid)

        # If no root_uri provided, parent is the root
        if root_uri is None:
            root_ref = parent_ref
        else:
            # Get root post CID
            root_post = client.app.bsky.feed.get_posts(params={"uris": [root_uri]})
            if not root_post.posts:
                raise ValueError("Root post not found")
            root_cid = root_post.posts[0].cid
            root_ref = models.ComAtprotoRepoStrongRef.Main(uri=root_uri, cid=root_cid)

        # Create the reply
        reply = client.send_post(
            text=text,
            reply_to=models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref),
        )

        return ReplyResult(
            success=True,
            uri=reply.uri,
            cid=reply.cid,
            parent_uri=parent_uri,
            root_uri=root_uri or parent_uri,
            error=None,
        )
    except Exception as e:
        return ReplyResult(
            success=False,
            uri=None,
            cid=None,
            parent_uri=None,
            root_uri=None,
            error=str(e),
        )


def create_post_with_rich_text(
    text: str,
    links: list[RichTextLink] | None = None,
    mentions: list[RichTextMention] | None = None,
) -> PostResult:
    """Create a post with rich text formatting (links and mentions)."""
    try:
        client = get_client()
        facets = []

        # Process links
        if links:
            for link in links:
                # Find the position of link text in the main text
                start = text.find(link["text"])
                if start == -1:
                    continue
                end = start + len(link["text"])

                facets.append(
                    models.AppBskyRichtextFacet.Main(
                        features=[models.AppBskyRichtextFacet.Link(uri=link["url"])],
                        index=models.AppBskyRichtextFacet.ByteSlice(
                            byte_start=len(text[:start].encode("UTF-8")),
                            byte_end=len(text[:end].encode("UTF-8")),
                        ),
                    )
                )

        # Process mentions
        if mentions:
            for mention in mentions:
                display_text = mention.get("display_text") or f"@{mention['handle']}"
                # Find the position of mention in the main text
                start = text.find(display_text)
                if start == -1:
                    continue
                end = start + len(display_text)

                # Resolve handle to DID
                resolved = client.app.bsky.actor.search_actors(
                    params={"q": mention["handle"], "limit": 1}
                )
                if not resolved.actors:
                    continue

                did = resolved.actors[0].did
                facets.append(
                    models.AppBskyRichtextFacet.Main(
                        features=[models.AppBskyRichtextFacet.Mention(did=did)],
                        index=models.AppBskyRichtextFacet.ByteSlice(
                            byte_start=len(text[:start].encode("UTF-8")),
                            byte_end=len(text[:end].encode("UTF-8")),
                        ),
                    )
                )

        # Send the post with facets
        post = client.send_post(text=text, facets=facets if facets else None)

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


def create_quote_post(text: str, quoted_uri: str) -> QuotePostResult:
    """Create a quote post."""
    try:
        client = get_client()

        # Get the post to quote
        quoted_post = client.app.bsky.feed.get_posts(params={"uris": [quoted_uri]})
        if not quoted_post.posts:
            raise ValueError("Quoted post not found")

        # Create strong ref for the quoted post
        quoted_cid = quoted_post.posts[0].cid
        quoted_ref = models.ComAtprotoRepoStrongRef.Main(uri=quoted_uri, cid=quoted_cid)

        # Create the embed
        embed = models.AppBskyEmbedRecord.Main(record=quoted_ref)

        # Send the quote post
        post = client.send_post(text=text, embed=embed)

        return QuotePostResult(
            success=True,
            uri=post.uri,
            cid=post.cid,
            quoted_uri=quoted_uri,
            error=None,
        )
    except Exception as e:
        return QuotePostResult(
            success=False,
            uri=None,
            cid=None,
            quoted_uri=None,
            error=str(e),
        )


def create_post_with_images(
    text: str,
    image_urls: list[str],
    alt_texts: list[str] | None = None,
) -> ImagePostResult:
    """Create a post with images from URLs."""
    try:
        client = get_client()
        import httpx

        # Ensure alt_texts has same length as images
        if alt_texts is None:
            alt_texts = [""] * len(image_urls)
        elif len(alt_texts) < len(image_urls):
            alt_texts.extend([""] * (len(image_urls) - len(alt_texts)))

        image_data = []
        image_alts = []
        for i, url in enumerate(image_urls[:4]):  # Max 4 images
            # Download image (follow redirects)
            response = httpx.get(url, follow_redirects=True)
            response.raise_for_status()

            image_data.append(response.content)
            image_alts.append(alt_texts[i] if i < len(alt_texts) else "")

        # Send post with images
        post = client.send_images(
            text=text,
            images=image_data,
            image_alts=image_alts,
        )

        return ImagePostResult(
            success=True,
            uri=post.uri,
            cid=post.cid,
            image_count=len(image_data),
            error=None,
        )
    except Exception as e:
        return ImagePostResult(
            success=False,
            uri=None,
            cid=None,
            image_count=0,
            error=str(e),
        )
