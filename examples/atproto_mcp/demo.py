"""Demo script showing ATProto MCP server capabilities."""

import argparse
import asyncio
import json
from typing import cast

from atproto_mcp.server import atproto_mcp
from atproto_mcp.types import (
    NotificationsResult,
    PostResult,
    ProfileInfo,
    SearchResult,
    TimelineResult,
)

from fastmcp import Client


async def main(enable_posting: bool = False):
    print("🔵 ATProto MCP Server Demo\n")

    async with Client(atproto_mcp) as client:
        # 1. Check connection status (resource)
        print("1. Checking connection status (resource)...")
        result = await client.read_resource("atproto://profile/status")
        status: ProfileInfo = (
            json.loads(result[0].text) if result else cast(ProfileInfo, {})
        )

        if status.get("connected"):
            print(f"✅ Connected as: @{status['handle']}")
            print(f"   Followers: {status['followers']}")
            print(f"   Following: {status['following']}")
            print(f"   Posts: {status['posts']}")
        else:
            print(f"❌ Connection failed: {status.get('error')}")
            return

        # 2. Get timeline (resource with parameter)
        print("\n2. Getting timeline (resource)...")
        result = await client.read_resource("atproto://timeline")
        timeline: TimelineResult = (
            json.loads(result[0].text) if result else cast(TimelineResult, {})
        )

        if timeline.get("success"):
            print(f"✅ Found {timeline['count']} posts:")
            for i, post in enumerate(timeline["posts"], 1):
                print(f"\n   Post {i}:")
                print(f"   Author: @{post['author']}")
                print(
                    f"   Text: {post['text'][:100]}..."
                    if post["text"] and len(post["text"]) > 100
                    else f"   Text: {post['text']}"
                )
                print(
                    f"   Likes: {post['likes']} | Reposts: {post['reposts']} | Replies: {post['replies']}"
                )
        else:
            print(f"❌ Failed to get timeline: {timeline.get('error')}")

        # 3. Search for posts (resource with template)
        print("\n3. Searching for posts about 'Python' (template resource)...")
        result = await client.read_resource("atproto://search/Python")
        search: SearchResult = (
            json.loads(result[0].text) if result else cast(SearchResult, {})
        )

        if search.get("success"):
            print(f"✅ Found {search['count']} posts about Python")
            if search["posts"]:
                post = search["posts"][0]
                print(
                    f"   Latest by @{post['author']}: {post['text'][:100]}..."
                    if post["text"] and len(post["text"]) > 100
                    else f"   Latest by @{post['author']}: {post['text']}"
                )
        else:
            print(f"❌ Search failed: {search.get('error')}")

        # 4. Get notifications (resource)
        print("\n4. Checking notifications (resource)...")
        result = await client.read_resource("atproto://notifications")
        notifs: NotificationsResult = (
            json.loads(result[0].text) if result else cast(NotificationsResult, {})
        )

        if notifs.get("success"):
            print(f"✅ You have {notifs['count']} recent notifications")
            unread = sum(1 for n in notifs["notifications"] if not n["is_read"])
            if unread:
                print(f"   ({unread} unread)")
        else:
            print(f"❌ Failed to get notifications: {notifs.get('error')}")

        # 5. Demo posting (tool)
        if enable_posting:
            print("\n5. Creating a test post (tool)...")
            post_result = await client.call_tool(
                "post_to_bluesky",
                {
                    "text": "🧪 Testing the ATProto MCP server demo! This post was created programmatically using FastMCP. #FastMCP #ATProto"
                },
            )
            result: PostResult = (
                json.loads(post_result[0].text) if post_result else cast(PostResult, {})
            )
            if result.get("success"):
                print("✅ Posted successfully!")
                print(f"   URI: {result['uri']}")
                print(f"   Created at: {result['created_at']}")
            else:
                print(f"❌ Failed to post: {result.get('error')}")
        else:
            print("\n5. Posting capability (tool):")
            print("   To enable posting, run with --post flag")
            print("   Example: python demo.py --post")

        # 6. Show available resources and tools
        print("\n6. Available capabilities:")
        print("   Resources (read-only):")
        print("     - atproto://profile/status - Profile information")
        print("     - atproto://timeline - Timeline feed")
        print("     - atproto://search/{query} - Search posts")
        print("     - atproto://notifications - Recent notifications")
        print("   Tools (actions):")
        print("     - post_to_bluesky - Create a new post")
        print("     - follow_user - Follow a user")
        print("     - like_post - Like a post")
        print("     - repost - Repost content")

        print("\n✨ Demo complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ATProto MCP Server Demo")
    parser.add_argument(
        "--post",
        action="store_true",
        help="Enable posting a test message to Bluesky",
    )
    args = parser.parse_args()

    asyncio.run(main(enable_posting=args.post))
