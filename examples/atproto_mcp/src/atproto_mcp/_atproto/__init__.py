"""Private ATProto implementation module."""

from ._client import get_client
from ._posts import create_post, create_thread
from ._profile import get_profile_info
from ._read import fetch_notifications, fetch_timeline, search_for_posts
from ._social import follow_user_by_handle, like_post_by_uri, repost_by_uri

__all__ = [
    "get_client",
    "get_profile_info",
    "create_post",
    "create_thread",
    "fetch_timeline",
    "search_for_posts",
    "fetch_notifications",
    "follow_user_by_handle",
    "like_post_by_uri",
    "repost_by_uri",
]
