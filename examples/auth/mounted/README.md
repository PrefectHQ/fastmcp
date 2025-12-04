# Multi-Provider OAuth Example

This example demonstrates mounting multiple OAuth-protected MCP servers in a single application, each with its own OAuth provider. It showcases RFC 8414 path-aware discovery where each server has its own authorization server metadata endpoint.

## URL Structure

- **GitHub MCP**: `http://localhost:8000/api/mcp/github/mcp`
- **Google MCP**: `http://localhost:8000/api/mcp/google/mcp`

Discovery endpoints (RFC 8414 path-aware):
- **GitHub**: `http://localhost:8000/.well-known/oauth-authorization-server/api/mcp/github`
- **Google**: `http://localhost:8000/.well-known/oauth-authorization-server/api/mcp/google`

## Setup

Set environment variables for both providers:

```bash
export FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID="your-github-client-id"
export FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET="your-github-client-secret"
export FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID="your-google-client-id"
export FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET="your-google-client-secret"
```

Configure redirect URIs in each provider's developer console (note the `/api/mcp/{provider}` prefix since the servers are mounted):
- GitHub: `http://localhost:8000/api/mcp/github/auth/callback/github`
- Google: `http://localhost:8000/api/mcp/google/auth/callback/google`

## Running

Start the server:
```bash
python server.py
```

Connect with the client:
```bash
python client.py
```
