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

- **post_to_bluesky**: Create new posts on Bluesky
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
        # Read resources
        status = await client.read_resource("atproto://profile/status")
        print(f"Connected as: {status}")
        
        timeline = await client.read_resource("atproto://timeline?limit=5")
        print(f"Timeline: {timeline}")
        
        # Use tools for actions
        post = await client.call_tool("post_to_bluesky", {
            "text": "Hello from FastMCP!"
        })
        print(f"Posted: {post}")
```

## Architecture

The server is organized with:
- `server.py` - Public API with resource and tool definitions
- `_atproto.py` - Private implementation details
- `types.py` - TypedDict definitions for structured responses
- `settings.py` - Configuration management

## Security Note

Store your Bluesky credentials securely in environment variables. Never commit credentials to version control.