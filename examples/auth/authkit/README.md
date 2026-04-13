# AuthKit Example

Protects a FastMCP server with WorkOS AuthKit. The server binds the JWT
`aud` claim to its own resource URL automatically — you just paste that same
URL into the WorkOS Dashboard as a resource indicator.

## WorkOS Dashboard setup

In the WorkOS Dashboard for your project, go to **Connect → Configuration** and:

1. Under **MCP Auth**, enable **Dynamic Client Registration** (or **Client ID
   Metadata Document** if your MCP client supports it).
2. Under **MCP resource indicators**, add `http://127.0.0.1:8000/mcp` as a
   valid resource indicator.

## Running

1. Set your AuthKit domain:

   ```bash
   export AUTHKIT_DOMAIN="https://your-app.authkit.app"
   ```

2. Start the server. It logs the resource URL it's validating against —
   that's the URL that must match your dashboard resource indicator:

   ```bash
   python server.py
   ```

3. In another terminal, run the client. Your browser will open for AuthKit
   authentication:

   ```bash
   python client.py
   ```
