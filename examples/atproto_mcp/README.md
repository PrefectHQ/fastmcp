# ATProto MCP Server

This example demonstrates a FastMCP server that provides tools and resources for interacting with the AT Protocol (Bluesky).

## Features

The server provides two types of capabilities:

### Resources (Read-only operations)

- **atproto://profile/status**: Get connection status and profile information
- **atproto://timeline**: Retrieve your timeline feed (last 10 posts)
- **atproto://search/{query}**: Search for posts by keyword
- **atproto://notifications**: Get recent notifications (last 10)

### Tools (Actions that modify state)

Basic interactions:
- **post_to_bluesky**: Create new posts on Bluesky
- **follow_user**: Follow a user by handle
- **like_post**: Like a post by URI
- **repost**: Repost content by URI

Advanced interactions:
- **reply_to_post**: Reply to posts and create threaded conversations
- **post_with_rich_text**: Create posts with clickable links and @mentions
- **quote_post**: Quote and comment on other posts
- **post_with_images**: Create posts with up to 4 images

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
pip install -e .

# Run the server
python -m atproto_mcp
```

## Usage Examples

### Basic Usage

```python
from fastmcp import Client
from atproto_mcp.server import atproto_mcp

async def demo():
    async with Client(atproto_mcp) as client:
        # Read resources
        status = await client.read_resource("atproto://profile/status")
        timeline = await client.read_resource("atproto://timeline")
        
        # Basic post
        post = await client.call_tool("post_to_bluesky", {
            "text": "Hello from FastMCP!"
        })
```

### Advanced Usage

```python
# Reply to a post
reply = await client.call_tool("reply_to_post", {
    "parent_uri": "at://did:plc:xxx/app.bsky.feed.post/yyy",
    "text": "Great point! Here's my perspective..."
})

# Post with rich text (links and mentions)
rich_post = await client.call_tool("post_with_rich_text", {
    "text": "Check out this article by @jlowin.dev",
    "links": [{"text": "this article", "url": "https://example.com"}],
    "mentions": [{"handle": "jlowin.dev", "display_text": "@jlowin.dev"}]
})

# Quote a post
quote = await client.call_tool("quote_post", {
    "text": "This is an important perspective on AI safety:",
    "quoted_uri": "at://did:plc:xxx/app.bsky.feed.post/yyy"
})

# Post with images
image_post = await client.call_tool("post_with_images", {
    "text": "Beautiful sunset today! 🌅",
    "image_urls": ["https://example.com/sunset.jpg"],
    "alt_texts": ["A sunset over the ocean"]
})
```

## AI Assistant Use Cases

This MCP server is designed to enable powerful AI assistant interactions:

- **"Reply to that post about climate change with these research findings"** - Uses reply_to_post with rich text links
- **"Share this article with my thoughts"** - Uses quote_post or post_with_rich_text
- **"Post this chart with an explanation"** - Uses post_with_images
- **"Start a discussion about AI safety and mention @expert.bsky"** - Uses post_with_rich_text with mentions

## Architecture

The server is organized with:
- `server.py` - Public API with resource and tool definitions
- `_atproto.py` - Private implementation details
- `types.py` - TypedDict definitions for structured responses
- `settings.py` - Configuration management

## Security Note

Store your Bluesky credentials securely in environment variables. Never commit credentials to version control.