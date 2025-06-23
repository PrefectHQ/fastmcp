"""Demo script showing all ATProto MCP server capabilities."""

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
        print("1. Checking connection status...")
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

        # 2. Get timeline
        print("\n2. Getting timeline...")
        result = await client.read_resource("atproto://timeline")
        timeline: TimelineResult = (
            json.loads(result[0].text) if result else cast(TimelineResult, {})
        )

        if timeline.get("success") and timeline["posts"]:
            print(f"✅ Found {timeline['count']} posts")
            post = timeline["posts"][0]
            print(f"   Latest by @{post['author']}: {post['text'][:80]}...")
            save_uri = post["uri"]  # Save for later interactions
        else:
            print("❌ No posts in timeline")
            save_uri = None

        # 3. Search for posts
        print("\n3. Searching for posts about 'Bluesky'...")
        result = await client.call_tool("search", {"query": "Bluesky", "limit": 5})
        search: SearchResult = (
            json.loads(result[0].text) if result else cast(SearchResult, {})
        )

        if search.get("success") and search["posts"]:
            print(f"✅ Found {search['count']} posts")
            print(f"   Sample: {search['posts'][0]['text'][:80]}...")

        # 4. Get notifications
        print("\n4. Checking notifications...")
        result = await client.read_resource("atproto://notifications")
        notifs: NotificationsResult = (
            json.loads(result[0].text) if result else cast(NotificationsResult, {})
        )

        if notifs.get("success"):
            print(f"✅ You have {notifs['count']} notifications")
            unread = sum(1 for n in notifs["notifications"] if not n["is_read"])
            if unread:
                print(f"   ({unread} unread)")

        # 5. Demo posting capabilities
        if enable_posting:
            print("\n5. Demonstrating posting capabilities...")

            # a. Simple post
            print("\n   a) Creating a simple post...")
            result = await client.call_tool(
                "post",
                {"text": "🧪 Testing the unified ATProto MCP post tool! #FastMCP"},
            )
            post_result: PostResult = json.loads(result[0].text) if result else {}
            if post_result.get("success"):
                print("   ✅ Posted successfully!")
                simple_uri = post_result["uri"]
            else:
                print(f"   ❌ Failed: {post_result.get('error')}")
                simple_uri = None

            # b. Post with rich text (link and mention)
            print("\n   b) Creating a post with rich text...")
            result = await client.call_tool(
                "post",
                {
                    "text": "Check out FastMCP and follow @alternatebuild.dev for updates!",
                    "links": [
                        {"text": "FastMCP", "url": "https://github.com/jlowin/fastmcp"}
                    ],
                    "mentions": [
                        {
                            "handle": "alternatebuild.dev",
                            "display_text": "@alternatebuild.dev",
                        }
                    ],
                },
            )
            if json.loads(result[0].text).get("success"):
                print("   ✅ Rich text post created!")

            # c. Reply to a post
            if save_uri:
                print("\n   c) Replying to a post...")
                result = await client.call_tool(
                    "post", {"text": "Great post! 👍", "reply_to": save_uri}
                )
                if json.loads(result[0].text).get("success"):
                    print("   ✅ Reply posted!")

            # d. Quote post
            if simple_uri:
                print("\n   d) Creating a quote post...")
                result = await client.call_tool(
                    "post",
                    {
                        "text": "Quoting my own test post for demo purposes 🔄",
                        "quote": simple_uri,
                    },
                )
                if json.loads(result[0].text).get("success"):
                    print("   ✅ Quote post created!")

            # e. Post with image
            print("\n   e) Creating a post with image...")
            result = await client.call_tool(
                "post",
                {
                    "text": "Here's a test image post! 📸",
                    "images": ["https://picsum.photos/400/300"],
                    "image_alts": ["Random test image"],
                },
            )
            if json.loads(result[0].text).get("success"):
                print("   ✅ Image post created!")

            # f. Quote with image (advanced)
            if simple_uri:
                print("\n   f) Creating a quote post with image...")
                result = await client.call_tool(
                    "post",
                    {
                        "text": "Quote + image combo! 🎨",
                        "quote": simple_uri,
                        "images": ["https://picsum.photos/300/200"],
                        "image_alts": ["Another test image"],
                    },
                )
                if json.loads(result[0].text).get("success"):
                    print("   ✅ Quote with image created!")

            # g. Social actions
            if save_uri:
                print("\n   g) Demonstrating social actions...")

                # Like
                result = await client.call_tool("like", {"uri": save_uri})
                if json.loads(result[0].text).get("success"):
                    print("   ✅ Liked a post!")

                # Repost
                result = await client.call_tool("repost", {"uri": save_uri})
                if json.loads(result[0].text).get("success"):
                    print("   ✅ Reposted!")

                # Follow
                result = await client.call_tool(
                    "follow", {"handle": "alternatebuild.dev"}
                )
                if json.loads(result[0].text).get("success"):
                    print("   ✅ Followed @alternatebuild.dev!")

            # h. Thread creation (new!)
            print("\n   h) Creating a thread...")
            result = await client.call_tool(
                "create_thread",
                {
                    "posts": [
                        {
                            "text": "Let me share some thoughts about the ATProto MCP server 🧵"
                        },
                        {
                            "text": "First, it makes posting from the terminal incredibly smooth"
                        },
                        {
                            "text": "The unified post API means one tool handles everything",
                            "links": [
                                {
                                    "text": "everything",
                                    "url": "https://github.com/jlowin/fastmcp",
                                }
                            ],
                        },
                        {
                            "text": "And now with create_thread, multi-post threads are trivial!"
                        },
                    ]
                },
            )
            if json.loads(result[0].text).get("success"):
                thread_result = json.loads(result[0].text)
                print(f"   ✅ Thread created with {thread_result['post_count']} posts!")
        else:
            print("\n5. Posting capabilities (not enabled):")
            print("   To test posting, run with --post flag")
            print("   Example: python demo.py --post")

        # 6. Show available capabilities
        print("\n6. Available capabilities:")
        print("\n   Resources (read-only):")
        print("     - atproto://profile/status")
        print("     - atproto://timeline")
        print("     - atproto://notifications")

        print("\n   Tools (actions):")
        print("     - post: Unified posting with rich features")
        print("       • Simple text posts")
        print("       • Images (up to 4)")
        print("       • Rich text (links, mentions)")
        print("       • Replies and threads")
        print("       • Quote posts")
        print("       • Combinations (quote + image, reply + rich text, etc.)")
        print("     - search: Search for posts")
        print("     - create_thread: Post multi-part threads")
        print("     - follow: Follow users")
        print("     - like: Like posts")
        print("     - repost: Share posts")

        print("\n✨ Demo complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ATProto MCP Server Demo")
    parser.add_argument(
        "--post",
        action="store_true",
        help="Enable posting test messages to Bluesky",
    )
    args = parser.parse_args()

    asyncio.run(main(enable_posting=args.post))
