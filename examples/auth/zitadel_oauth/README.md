# ZITADEL OAuth Example

Demonstrates FastMCP server protection with ZITADEL through the OAuth proxy.

## Setup

1. Create a ZITADEL web application:
   - In the ZITADEL Console, create a project (e.g., `My MCP Server`)
   - In the project, create a new application: enter a name (e.g.,
     `FastMCP OAuth Example`), choose type **Web**, then authentication method
     **Code** (the method that issues a client secret)
   - Add the redirect URI `http://127.0.0.1:8000/auth/callback` and enable
     **Development Mode** so ZITADEL accepts the `http://` URI
   - Note the client ID, the client secret (shown only once), and your instance
     domain
   - Optionally enable refresh tokens. The creation wizard cannot set grant
     types, so open the created application, go to **Settings** →
     **OIDC Settings** → **Grant Types**, tick **Refresh Token** next to
     **Authorization Code**, and click **Save**. Then add `offline_access` to
     `required_scopes` in `server.py`

2. Set environment variables:

   ```bash
   export FASTMCP_SERVER_AUTH_ZITADEL_DOMAIN="https://my-instance-abc123.zitadel.cloud"
   export FASTMCP_SERVER_AUTH_ZITADEL_CLIENT_ID="your-client-id"
   export FASTMCP_SERVER_AUTH_ZITADEL_CLIENT_SECRET="your-client-secret"
   ```

   Or create a `.env` file:

   ```env
   FASTMCP_SERVER_AUTH_ZITADEL_DOMAIN=https://my-instance-abc123.zitadel.cloud
   FASTMCP_SERVER_AUTH_ZITADEL_CLIENT_ID=your-client-id
   FASTMCP_SERVER_AUTH_ZITADEL_CLIENT_SECRET=your-client-secret
   ```

3. Run the server:

   ```bash
   uv run python server.py
   ```

4. In another terminal, run the client:

   ```bash
   uv run python client.py
   ```

The client will open your browser for ZITADEL authentication.
