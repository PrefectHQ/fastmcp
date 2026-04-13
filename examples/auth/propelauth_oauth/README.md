# PropelAuth OAuth Example

Demonstrates FastMCP server protection with PropelAuth OAuth.

## Setup

### 1. Configure MCP Authentication in PropelAuth

**Create a PropelAuth Account**:

- Go to [PropelAuth Dashboard](https://www.propelauth.com)
- Navigate to the **MCP** section and click **Enable MCP**

**Configure Allowed MCP Clients**:

- Under **MCP > Allowed MCP Clients**, add redirect URIs for each MCP client you want to allow
- PropelAuth provides templates for popular clients like Claude, Cursor, and ChatGPT

**Configure Scopes**:

- Under **MCP > Scopes**, define the permissions available to MCP clients (e.g., `read:user_data`)

**Generate Introspection Credentials**:

- Go to **MCP > Request Validation** and click **Create Credentials**
- Note the **Client ID** and **Client Secret**

**Note Your Auth URL**:

- Find your Auth URL in the **Backend Integration** section (e.g., `https://auth.yourdomain.com`)

Create a `.env` file:

```bash
# Required PropelAuth credentials
PROPELAUTH_AUTH_URL=https://auth.yourdomain.com
PROPELAUTH_INTROSPECTION_CLIENT_ID=your-client-id
PROPELAUTH_INTROSPECTION_CLIENT_SECRET=your-client-secret
BASE_URL=http://127.0.0.1:8000/
# Optional: additional scopes tokens must include (comma-separated)
# PROPELAUTH_REQUIRED_SCOPES=read:user_data
```

### 2. Run the Example

Start the server:

```bash
# From this directory
uv run python server.py
```

The server will start on `http://127.0.0.1:8000/mcp` with PropelAuth OAuth authentication enabled.

Test with client:

```bash
uv run python client.py
```

The `client.py` will:

1. Attempt to connect to the server
2. Detect that OAuth authentication is required
3. Open a browser for PropelAuth authentication
4. Complete the OAuth flow and connect to the server
5. Demonstrate calling authenticated tools (echo and whoami)
