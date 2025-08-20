# WorkOS OAuth Example

This example demonstrates how to use the WorkOS OAuth provider with FastMCP servers.

## Overview

The WorkOS OAuth provider enables authentication using WorkOS User Management. It provides general OAuth2 authentication similar to GitHub or Google, with optional support for enterprise SSO connections. Unlike the AuthKit provider which uses DCR (Dynamic Client Registration), this provider works with traditional OAuth flows.

## Setup

### 1. WorkOS Configuration

1. **Create a WorkOS Application**:
   - Go to [WorkOS Dashboard → Applications](https://dashboard.workos.com/applications)
   - Create a new application or use an existing one
   - Enable **User Management** for OAuth authentication
   - Copy your `Client ID` and `API Key` (client secret)

2. **Configure SSO Connection** (optional for enterprise SSO):
   - Go to WorkOS Dashboard → Connections
   - Set up your SSO connection (SAML, OIDC, or OAuth provider like Google/Microsoft)
   - Note the `Organization ID` or `Connection ID` if using SSO

3. **Set Redirect URLs**:
   - In your WorkOS application settings, add redirect URLs for your OAuth flow
   - For this example: `http://localhost:8000/auth/callback`

### 2. Environment Variables

Create a `.env` file in this directory:

```bash
# Required WorkOS credentials
WORKOS_CLIENT_ID=client_123
WORKOS_API_KEY=sk_test_456  # Your WorkOS API key (client secret)

# Server URL (optional, defaults to http://localhost:8000)
# WORKOS_BASE_URL=http://localhost:8000

# Optional: For enterprise SSO connections
# WORKOS_ORGANIZATION_ID=org_123  # Route to specific organization's SSO
# WORKOS_CONNECTION_ID=conn_456    # Route to specific SSO connection

# Optional: Required scopes
# FASTMCP_SERVER_AUTH_WORKOS_REQUIRED_SCOPES=["profile", "email"]
```

### 3. Install Dependencies

```bash
cd /Users/jlowin/Developer/fastmcp
uv sync
```

## Running the Example

### Start the Server

```bash
# From this directory
uv run python server.py
```

The server will start on `http://localhost:8000` with WorkOS OAuth authentication enabled.

### Test with Client

In another terminal:

```bash
# From this directory  
uv run python client.py
```

The client will:
1. Attempt to connect to the server
2. Detect that OAuth authentication is required
3. Open a browser for WorkOS authentication
4. Complete the OAuth flow and connect to the server
5. Demonstrate calling authenticated tools

## How It Works

### Authentication Flow

1. **Client Request**: Client attempts to connect to FastMCP server
2. **Auth Challenge**: Server responds with `401 Unauthorized` and `WWW-Authenticate` header
3. **OAuth Discovery**: Client discovers OAuth endpoints from server metadata
4. **Authorization**: Client redirects user to WorkOS for authentication
5. **Callback**: WorkOS redirects back with authorization code
6. **Token Exchange**: Client exchanges code for access token
7. **API Calls**: Client uses access token for authenticated MCP requests

### Server Components

- **WorkOSProvider**: Validates tokens using WorkOS User Management API
- **Protected Resources**: MCP tools and resources require valid WorkOS tokens
- **OAuth Metadata**: Server advertises WorkOS as authorization server

### Client Components  

- **OAuth Client**: Handles browser-based OAuth flow
- **Token Storage**: Caches tokens for future use
- **Automatic Auth**: Transparently handles authentication

## Key Features

- **SSO Integration**: Works with any WorkOS SSO connection
- **User Management**: Validates tokens against WorkOS User Management API
- **Token Caching**: Reuses tokens across sessions
- **Error Handling**: Graceful handling of auth failures and token expiration

## Troubleshooting

### Common Issues

1. **"Invalid client" error**: Check CLIENT_ID and CLIENT_SECRET
2. **"Token validation failed"**: Check API_KEY and token scope
3. **"Redirect URI mismatch"**: Ensure redirect URL matches WorkOS settings
4. **Browser doesn't open**: Check firewall settings for localhost

### Debug Mode

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Token Inspection

Check cached tokens:

```bash
ls ~/.fastmcp/oauth-mcp-client-cache/
```

Clear token cache:

```python
from fastmcp.client.auth.oauth import FileTokenStorage
FileTokenStorage.clear_all()
```

## Security Notes

- Never commit `.env` files with real credentials
- Use HTTPS in production
- Rotate API keys regularly
- Monitor WorkOS logs for unusual activity
- Set appropriate token expiration times

## Next Steps

- Explore WorkOS Directory Sync for user provisioning
- Set up multi-organization support
- Implement role-based access control
- Add custom scopes and claims validation