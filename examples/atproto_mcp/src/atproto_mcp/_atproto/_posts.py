"""Unified posting functionality."""

import time
from datetime import datetime

from atproto import models

from atproto_mcp.types import (
    PostResult,
    RichTextLink,
    RichTextMention,
    ThreadPost,
    ThreadResult,
)

from ._client import get_client


def create_post(
    text: str,
    images: list[str] | None = None,
    image_alts: list[str] | None = None,
    links: list[RichTextLink] | None = None,
    mentions: list[RichTextMention] | None = None,
    reply_to: str | None = None,
    reply_root: str | None = None,
    quote: str | None = None,
) -> PostResult:
    """Create a unified post with optional features.

    Args:
        text: Post text (max 300 chars)
        images: URLs of images to attach (max 4)
        image_alts: Alt text for images
        links: Links to embed in rich text
        mentions: User mentions to embed
        reply_to: URI of post to reply to
        reply_root: URI of thread root (defaults to reply_to)
        quote: URI of post to quote
    """
    try:
        client = get_client()
        facets = []
        embed = None
        reply_ref = None

        # Always build facets to handle auto-detected URLs and explicit links/mentions
        facets = _build_facets(text, links, mentions, client)

        # Handle replies
        if reply_to:
            reply_ref = _build_reply_ref(reply_to, reply_root, client)

        # Handle quotes and images
        if quote and images:
            # Quote with images - create record with media embed
            embed = _build_quote_with_images_embed(quote, images, image_alts, client)
        elif quote:
            # Quote only
            embed = _build_quote_embed(quote, client)
        elif images:
            # Images only - use send_images for proper handling
            return _send_images(text, images, image_alts, facets, reply_ref, client)

        # Send the post (always include facets if any were created)
        post = client.send_post(
            text=text,
            facets=facets if facets else None,
            embed=embed,
            reply_to=reply_ref,
        )

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


def _build_facets(
    text: str,
    links: list[RichTextLink] | None,
    mentions: list[RichTextMention] | None,
    client,
):
    """Build facets for rich text formatting, including auto-detected URLs."""
    import re

    facets = []
    covered_ranges = []

    # URL regex pattern for auto-detection
    url_pattern = re.compile(
        r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&/=]*)"
    )

    # Process explicit links first
    if links:
        for link in links:
            start = text.find(link["text"])
            if start == -1:
                continue
            end = start + len(link["text"])

            # Track this range as covered
            covered_ranges.append((start, end))

            facets.append(
                models.AppBskyRichtextFacet.Main(
                    features=[models.AppBskyRichtextFacet.Link(uri=link["url"])],
                    index=models.AppBskyRichtextFacet.ByteSlice(
                        byte_start=len(text[:start].encode("UTF-8")),
                        byte_end=len(text[:end].encode("UTF-8")),
                    ),
                )
            )

    # Auto-detect URLs that aren't already covered by explicit links
    for match in url_pattern.finditer(text):
        url = match.group()
        start = match.start()
        end = match.end()

        # Check if this URL overlaps with any explicit link
        overlaps = False
        for covered_start, covered_end in covered_ranges:
            if not (end <= covered_start or start >= covered_end):
                overlaps = True
                break

        if not overlaps:
            facets.append(
                models.AppBskyRichtextFacet.Main(
                    features=[models.AppBskyRichtextFacet.Link(uri=url)],
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

    return facets


def _build_reply_ref(reply_to: str, reply_root: str | None, client):
    """Build reply reference."""
    # Get parent post to extract CID
    parent_post = client.app.bsky.feed.get_posts(params={"uris": [reply_to]})
    if not parent_post.posts:
        raise ValueError("Parent post not found")

    parent_cid = parent_post.posts[0].cid
    parent_ref = models.ComAtprotoRepoStrongRef.Main(uri=reply_to, cid=parent_cid)

    # If no root_uri provided, parent is the root
    if reply_root is None:
        root_ref = parent_ref
    else:
        # Get root post CID
        root_post = client.app.bsky.feed.get_posts(params={"uris": [reply_root]})
        if not root_post.posts:
            raise ValueError("Root post not found")
        root_cid = root_post.posts[0].cid
        root_ref = models.ComAtprotoRepoStrongRef.Main(uri=reply_root, cid=root_cid)

    return models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)


def _build_quote_embed(quote_uri: str, client):
    """Build quote embed."""
    # Get the post to quote
    quoted_post = client.app.bsky.feed.get_posts(params={"uris": [quote_uri]})
    if not quoted_post.posts:
        raise ValueError("Quoted post not found")

    # Create strong ref for the quoted post
    quoted_cid = quoted_post.posts[0].cid
    quoted_ref = models.ComAtprotoRepoStrongRef.Main(uri=quote_uri, cid=quoted_cid)

    # Create the embed
    return models.AppBskyEmbedRecord.Main(record=quoted_ref)


def _build_quote_with_images_embed(
    quote_uri: str, image_urls: list[str], image_alts: list[str] | None, client
):
    """Build quote embed with images."""
    import httpx

    # Get the quoted post
    quoted_post = client.app.bsky.feed.get_posts(params={"uris": [quote_uri]})
    if not quoted_post.posts:
        raise ValueError("Quoted post not found")

    quoted_cid = quoted_post.posts[0].cid
    quoted_ref = models.ComAtprotoRepoStrongRef.Main(uri=quote_uri, cid=quoted_cid)

    # Download and upload images
    images = []
    alts = image_alts or [""] * len(image_urls)

    for i, url in enumerate(image_urls[:4]):
        response = httpx.get(url, follow_redirects=True)
        response.raise_for_status()

        # Upload to blob storage
        upload = client.upload_blob(response.content)
        images.append(
            models.AppBskyEmbedImages.Image(
                alt=alts[i] if i < len(alts) else "",
                image=upload.blob,
            )
        )

    # Create record with media embed
    return models.AppBskyEmbedRecordWithMedia.Main(
        record=models.AppBskyEmbedRecord.Main(record=quoted_ref),
        media=models.AppBskyEmbedImages.Main(images=images),
    )


def _send_images(
    text: str,
    image_urls: list[str],
    image_alts: list[str] | None,
    facets,
    reply_ref,
    client,
):
    """Send post with images using the client's send_images method."""
    import httpx

    # Ensure alt_texts has same length as images
    if image_alts is None:
        image_alts = [""] * len(image_urls)
    elif len(image_alts) < len(image_urls):
        image_alts.extend([""] * (len(image_urls) - len(image_alts)))

    image_data = []
    alts = []
    for i, url in enumerate(image_urls[:4]):  # Max 4 images
        # Download image (follow redirects)
        response = httpx.get(url, follow_redirects=True)
        response.raise_for_status()

        image_data.append(response.content)
        alts.append(image_alts[i] if i < len(image_alts) else "")

    # Send post with images
    # Note: send_images doesn't support facets or reply_to directly
    # So we need to use send_post with manual image upload if we have facets or replies
    # Since we always create facets now (for URL auto-detection), we'll always use this path
    if facets or reply_ref:
        # Manual image upload
        images = []
        for i, data in enumerate(image_data):
            upload = client.upload_blob(data)
            images.append(
                models.AppBskyEmbedImages.Image(
                    alt=alts[i],
                    image=upload.blob,
                )
            )

        embed = models.AppBskyEmbedImages.Main(images=images)
        post = client.send_post(
            text=text,
            facets=facets if facets else None,
            embed=embed,
            reply_to=reply_ref,
        )
    else:
        # Use simple send_images
        post = client.send_images(
            text=text,
            images=image_data,
            image_alts=alts,
        )

    return PostResult(
        success=True,
        uri=post.uri,
        cid=post.cid,
        text=text,
        created_at=datetime.now().isoformat(),
        error=None,
    )


def create_thread(posts: list[ThreadPost]) -> ThreadResult:
    """Create a thread of posts with automatic linking.

    Args:
        posts: List of posts to create as a thread. First post is the root.
    """
    if not posts:
        return ThreadResult(
            success=False,
            thread_uri=None,
            post_uris=[],
            post_count=0,
            error="No posts provided",
        )

    try:
        post_uris = []
        root_uri = None
        parent_uri = None

        for i, post_data in enumerate(posts):
            # First post is the root
            if i == 0:
                result = create_post(
                    text=post_data["text"],
                    images=post_data.get("images"),
                    image_alts=post_data.get("image_alts"),
                    links=post_data.get("links"),
                    mentions=post_data.get("mentions"),
                    quote=post_data.get("quote"),
                )

                if not result["success"]:
                    return ThreadResult(
                        success=False,
                        thread_uri=None,
                        post_uris=post_uris,
                        post_count=len(post_uris),
                        error=f"Failed to create root post: {result['error']}",
                    )

                root_uri = result["uri"]
                parent_uri = root_uri
                post_uris.append(root_uri)

                # Small delay to ensure post is indexed
                time.sleep(0.5)
            else:
                # Subsequent posts reply to the previous one
                result = create_post(
                    text=post_data["text"],
                    images=post_data.get("images"),
                    image_alts=post_data.get("image_alts"),
                    links=post_data.get("links"),
                    mentions=post_data.get("mentions"),
                    quote=post_data.get("quote"),
                    reply_to=parent_uri,
                    reply_root=root_uri,
                )

                if not result["success"]:
                    return ThreadResult(
                        success=False,
                        thread_uri=root_uri,
                        post_uris=post_uris,
                        post_count=len(post_uris),
                        error=f"Failed to create post {i + 1}: {result['error']}",
                    )

                parent_uri = result["uri"]
                post_uris.append(parent_uri)

                # Small delay between posts
                if i < len(posts) - 1:
                    time.sleep(0.5)

        return ThreadResult(
            success=True,
            thread_uri=root_uri,
            post_uris=post_uris,
            post_count=len(post_uris),
            error=None,
        )

    except Exception as e:
        return ThreadResult(
            success=False,
            thread_uri=None,
            post_uris=post_uris,
            post_count=len(post_uris),
            error=str(e),
        )
