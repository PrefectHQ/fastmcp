"""Profile-related operations."""

from atproto_mcp.types import ProfileInfo

from ._client import get_client


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
