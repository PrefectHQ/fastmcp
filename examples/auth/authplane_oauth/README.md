# Authplane OAuth Example

Demonstrates FastMCP server protection with [Authplane](https://github.com/AuthPlane/authserver),
a self-hosted OAuth 2.1 authorization server for MCP.

Authplane supports Dynamic Client Registration, so MCP clients register
themselves at runtime — no pre-provisioned `client_id` is needed.

## Setup

1. Run Authplane (Docker):

   ```bash
   export AUTHPLANE_ADMIN_API_KEY="$(openssl rand -hex 32)"
   export AUTHPLANE_SESSION_SECRET="$(openssl rand -hex 32)"

   docker run -p 9000:9000 -p 9001:9001 \
     -e AUTHPLANE_ADMIN_API_KEY -e AUTHPLANE_SESSION_SECRET \
     authplane/authserver:latest serve
   ```

   Public OAuth endpoints are on `:9000`; the Admin UI is at
   `http://localhost:9001/admin/ui/`.

2. Register this server's resource. The `uri` must match `base_url` + the MCP
   path (`/mcp`) exactly, and must declare the scopes the server requires:

   ```bash
   curl -X POST http://localhost:9001/admin/resources \
     -H "Authorization: Bearer $AUTHPLANE_ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "slug": "example",
       "uri": "http://127.0.0.1:8000/mcp",
       "backend_kind": "mint",
       "display_name": "Authplane Example",
       "scopes": [{"name": "tools/read", "description": "Read access"}]
     }'
   ```

3. Create a user to sign in as (the client opens a browser to log in):

   ```bash
   curl -X POST http://localhost:9001/admin/users \
     -H "Authorization: Bearer $AUTHPLANE_ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "email": "user@example.com",
       "name": "Example User",
       "password": "changeme",
       "role": "user"
     }'
   ```

4. Point the server at your Authplane instance:

   ```bash
   export AUTHPLANE_ISSUER="http://localhost:9000"
   ```

5. Run the server:

   ```bash
   python server.py
   ```

6. In another terminal, run the client:

   ```bash
   python client.py
   ```

The client opens your browser for Authplane authentication — sign in with the
email and password from step 3. It then calls the protected
`get_access_token_claims` tool.

## Notes

- The server accepts tokens signed with **ES256** (Authplane's default) or
  **RS256**.
- The token's audience is bound to `http://127.0.0.1:8000/mcp` (RFC 8707), so a
  token minted for a different resource will not work here.
