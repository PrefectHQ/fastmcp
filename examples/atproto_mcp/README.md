# ATProto MCP Server

This example demonstrates a FastMCP server that provides tools for interacting with the AT Protocol (Bluesky).

## Features

The server provides the following tools:

- **atproto_status**: Check connection status and profile information
- **post_to_bluesky**: Create new posts on Bluesky
- **get_timeline**: Retrieve your timeline feed
- **search_posts**: Search for posts by keyword
- **get_notifications**: Get recent notifications
- **follow_user**: Follow a user by handle
- **like_post**: Like a post by URI
- **repost**: Repost content by URI

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

## Usage Example

```python
from fastmcp import Client
from atproto_mcp.server import atproto_mcp

async def demo():
    async with Client(atproto_mcp) as client:
        # Check status
        status = await client.call_tool("atproto_status", {})
        print(f"Connected as: {status['handle']}")
        
        # Post to Bluesky
        post = await client.call_tool("post_to_bluesky", {
            "text": "Hello from FastMCP!"
        })
        print(f"Posted: {post['uri']}")
        
        # Get timeline
        timeline = await client.call_tool("get_timeline", {"limit": 5})
        print(f"Found {timeline['count']} posts")
        
        # Search posts
        results = await client.call_tool("search_posts", {
            "query": "FastMCP",
            "limit": 10
        })
        print(f"Found {results['count']} posts matching 'FastMCP'")
```

## Security Note

Store your Bluesky credentials securely in environment variables. Never commit credentials to version control.