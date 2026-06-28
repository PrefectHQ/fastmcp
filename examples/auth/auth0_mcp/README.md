# Auth0 Auth for MCP Example

Protects a FastMCP server with Auth0 [Auth for MCP](https://auth0.com/ai/docs/mcp/intro/overview). Auth0 handles OAuth and client registration; FastMCP validates access tokens.

## Auth0 setup

1. Enable **Resource Parameter Compatibility Profile** (Settings → Advanced).
2. Create an API whose identifier is `http://127.0.0.1:8000/mcp` (must match the URL logged at server startup).
3. Promote your login connections to domain-level (required for third-party DCR clients).

See Auth0's [authorization quickstart](https://auth0.com/ai/docs/mcp/get-started/authorization-for-your-mcp-server) for details.

## Running

```bash
export AUTH0_CONFIG_URL="https://YOUR_TENANT.auth0.com/.well-known/openid-configuration"
python server.py
```

In another terminal:

```bash
python client.py
```

Use `127.0.0.1` consistently — mixing `localhost` and `127.0.0.1` breaks audience validation.

For troubleshooting (DCR grants, token exchange errors, MCP Inspector), see the [Auth0 integration guide](https://gofastmcp.com/integrations/auth0).
