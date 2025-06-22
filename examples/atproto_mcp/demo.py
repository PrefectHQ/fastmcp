"""Demo script showing ATProto MCP server capabilities."""

import argparse
import asyncio
import json

from atproto_mcp.server import atproto_mcp

from fastmcp import Client


async def main(enable_posting: bool = False):
    print("🔵 ATProto MCP Server Demo\n")

    async with Client(atproto_mcp) as client:
        # 1. Check connection status
        print("1. Checking connection status...")
        result = await client.call_tool("atproto_status", {})
        status = json.loads(result[0].text) if result else {}

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
        result = await client.call_tool("get_timeline", {"limit": 3})
        timeline = json.loads(result[0].text) if result else {}

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
        result = await client.call_tool("search_posts", {"query": "Python", "limit": 3})
        search = json.loads(result[0].text) if result else {}

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
        result = await client.call_tool("get_notifications", {"limit": 5})
        notifs = json.loads(result[0].text) if result else {}

        if notifs.get("success"):
            print(f"✅ You have {notifs['count']} recent notifications")
            unread = sum(1 for n in notifs["notifications"] if not n["is_read"])
            if unread:
                print(f"   ({unread} unread)")
        else:
            print(f"❌ Failed to get notifications: {notifs.get('error')}")

        # 5. Demo posting
        if enable_posting:
            print("\n5. Creating a test post...")
            post = await client.call_tool(
                "post_to_bluesky",
                {
                    "text": "🧪 Testing the ATProto MCP server demo! This post was created programmatically using FastMCP. #FastMCP #ATProto"
                },
            )
            result = json.loads(post[0].text) if post else {}
            if result.get("success"):
                print("✅ Posted successfully!")
                print(f"   URI: {result['uri']}")
                print(f"   Created at: {result['created_at']}")
            else:
                print(f"❌ Failed to post: {result.get('error')}")
        else:
            print("\n5. Posting capability:")
            print("   To enable posting, run with --post flag")
            print("   Example: python demo.py --post")

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
