#!/usr/bin/env python3
"""
Demo script showing ATProto MCP server capabilities.
"""

import asyncio
import sys
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from atproto_mcp.server import atproto_mcp

from fastmcp import Client


async def main():
    print("🔵 ATProto MCP Server Demo\n")

    async with Client(atproto_mcp) as client:
        # 1. Check connection status
        print("1. Checking connection status...")
        status = await client.call_tool("atproto_status", {})

        if status.get("connected"):
            print(f"✅ Connected as: @{status['handle']}")
            print(f"   Followers: {status['followers']}")
            print(f"   Following: {status['following']}")
            print(f"   Posts: {status['posts']}")
        else:
            print(f"❌ Connection failed: {status.get('error')}")
            return

        # 2. Get timeline
        print("\n2. Getting timeline (last 3 posts)...")
        timeline = await client.call_tool("get_timeline", {"limit": 3})

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

        # 3. Search for posts
        print("\n3. Searching for posts about 'Python'...")
        search = await client.call_tool("search_posts", {"query": "Python", "limit": 3})

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

        # 4. Get notifications
        print("\n4. Checking notifications...")
        notifs = await client.call_tool("get_notifications", {"limit": 5})

        if notifs.get("success"):
            print(f"✅ You have {notifs['count']} recent notifications")
            unread = sum(1 for n in notifs["notifications"] if not n["is_read"])
            if unread:
                print(f"   ({unread} unread)")
        else:
            print(f"❌ Failed to get notifications: {notifs.get('error')}")

        # 5. Demo posting (commented out to avoid spam)
        print("\n5. Posting capability:")
        print("   To post, you would use:")
        print(
            '   await client.call_tool("post_to_bluesky", {"text": "Hello from FastMCP! 🚀"})'
        )

        # Uncomment to actually post:
        # post = await client.call_tool("post_to_bluesky", {
        #     "text": "Testing ATProto MCP server with FastMCP! 🚀"
        # })
        # if post.get("success"):
        #     print(f"✅ Posted successfully: {post['uri']}")

        print("\n✨ Demo complete!")


if __name__ == "__main__":
    asyncio.run(main())
