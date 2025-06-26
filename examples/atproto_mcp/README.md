# ATProto MCP Server

This example demonstrates a FastMCP server that provides tools and resources for interacting with the AT Protocol (Bluesky).

## Features

### Resources (Read-only)

- **atproto://profile/status**: Get connection status and profile information
- **atproto://timeline**: Retrieve your timeline feed
- **atproto://notifications**: Get recent notifications

### Tools (Actions)

- **post**: Create posts with rich features (text, images, quotes, replies, links, mentions)
- **create_thread**: Post multi-part threads with automatic linking
- **search**: Search for posts by query
- **follow**: Follow users by handle
- **like**: Like posts by URI
- **repost**: Share posts by URI

## Setup

1. Create a `.env` file in the root directory with your Bluesky credentials:

```bash
ATPROTO_HANDLE=your.handle@bsky.social
ATPROTO_PASSWORD=your-app-password
ATPROTO_PDS_URL=https://bsky.social  # optional, defaults to bsky.social
```

2. Install and run the server:

```bash
# Install dependencies
uv pip install -e .

# Run the server
uv run atproto-mcp
```

## The Unified Post Tool

The `post` tool is a single, flexible interface for all posting needs:

```python
async def post(
    text: str,                          # Required: Post content
    images: list[str] = None,           # Optional: Image URLs (max 4)
    image_alts: list[str] = None,       # Optional: Alt text for images
    links: list[RichTextLink] = None,   # Optional: Embedded links
    mentions: list[RichTextMention] = None,  # Optional: User mentions
    reply_to: str = None,               # Optional: Reply to post URI
    reply_root: str = None,             # Optional: Thread root URI
    quote: str = None,                  # Optional: Quote post URI
)
```

### Usage Examples

```python
from fastmcp import Client
from atproto_mcp.server import atproto_mcp

async def demo():
    async with Client(atproto_mcp) as client:
        # Simple post
        await client.call_tool("post", {
            "text": "Hello from FastMCP!"
        })
        
        # Post with image
        await client.call_tool("post", {
            "text": "Beautiful sunset! 🌅",
            "images": ["https://example.com/sunset.jpg"],
            "image_alts": ["Sunset over the ocean"]
        })
        
        # Reply to a post
        await client.call_tool("post", {
            "text": "Great point!",
            "reply_to": "at://did:plc:xxx/app.bsky.feed.post/yyy"
        })
        
        # Quote post
        await client.call_tool("post", {
            "text": "This is important:",
            "quote": "at://did:plc:xxx/app.bsky.feed.post/yyy"
        })
        
        # Rich text with links and mentions
        await client.call_tool("post", {
            "text": "Check out FastMCP by @alternatebuild.dev",
            "links": [{"text": "FastMCP", "url": "https://github.com/jlowin/fastmcp"}],
            "mentions": [{"handle": "alternatebuild.dev", "display_text": "@alternatebuild.dev"}]
        })
        
        # Advanced: Quote with image
        await client.call_tool("post", {
            "text": "Adding visual context:",
            "quote": "at://did:plc:xxx/app.bsky.feed.post/yyy",
            "images": ["https://example.com/chart.png"]
        })
        
        # Advanced: Reply with rich text
        await client.call_tool("post", {
            "text": "I agree! See this article for more info",
            "reply_to": "at://did:plc:xxx/app.bsky.feed.post/yyy",
            "links": [{"text": "this article", "url": "https://example.com/article"}]
        })
        
        # Create a thread
        await client.call_tool("create_thread", {
            "posts": [
                {"text": "Starting a thread about Python 🧵"},
                {"text": "Python is great for rapid prototyping"},
                {"text": "And the ecosystem is amazing!", "images": ["https://example.com/python.jpg"]}
            ]
        })
```

## AI Assistant Use Cases

The unified API enables natural AI assistant interactions:

- **"Reply to that post with these findings"** → Uses `reply_to` with rich text
- **"Share this article with commentary"** → Uses `quote` with the article link
- **"Post this chart with explanation"** → Uses `images` with descriptive text
- **"Start a thread about AI safety"** → Uses `create_thread` for automatic linking

## Architecture

The server is organized as:
- `server.py` - Public API with resources and tools
- `_atproto/` - Private implementation module
  - `_client.py` - ATProto client management
  - `_posts.py` - Unified posting logic
  - `_profile.py` - Profile operations
  - `_read.py` - Timeline, search, notifications
  - `_social.py` - Follow, like, repost
- `types.py` - TypedDict definitions
- `settings.py` - Configuration management

## Running the Demo

```bash
# Run demo (read-only)
uv run python demo.py

# Run demo with posting enabled
uv run python demo.py --post
```

## Security Note

Store your Bluesky credentials securely in environment variables. Never commit credentials to version control.