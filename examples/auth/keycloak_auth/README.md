# Keycloak OAuth Example

Demonstrates FastMCP server protection with Keycloak OAuth.

## Setup

### 1. Prepare the Realm Configuration

Review the realm configuration file: [`keycloak/realm-fastmcp.json`](keycloak/realm-fastmcp.json)

**Optional**: Customize the file for your environment:
- **Realm name**: Change `"realm": "fastmcp"` to match your project
- **Trusted hosts**: Update the `"trusted-hosts"` section for your environment
- **Test user**: Review credentials (`testuser` / `password123`) and change for security

### 2. Set Up Keycloak

Choose one of the following options:

#### Option A: Local Keycloak Instance (Recommended for Testing)

See [keycloak/README.md](keycloak/README.md) for details.

**Note:** The realm will be automatically imported on startup.

#### Option B: Existing Keycloak Instance

Manually import the realm:
- Log in to your Keycloak Admin Console
- Click **Manage realms** → **Create realm**
- Drag the `realm-fastmcp.json` file into the **Resource file** box
- Click **Create**

### 3. Run the Example

1. (Optional) Set environment variables if you want to customize the configuration:

   ```bash
   export FASTMCP_SERVER_AUTH_KEYCLOAK_REALM_URL="http://localhost:8080/realms/fastmcp"
   export FASTMCP_SERVER_AUTH_KEYCLOAK_BASE_URL="http://localhost:8000"
   # Optional: Set audience for token validation (disabled by default)
   # For production, configure Keycloak audience mappers first, then uncomment:
   # export FASTMCP_SERVER_AUTH_KEYCLOAK_AUDIENCE="http://localhost:8000"
   ```

   The server uses sensible defaults, so you can skip this step if you're using the local setup.

2. Run the server:

   ```bash
   python server.py
   ```

3. Test the server:

   You have two options to test the OAuth-protected server:

   **Option A: Using the Python Client (Programmatic)**

   In another terminal, run the example client:

   ```bash
   python client.py
   ```

   The client will open your browser for Keycloak authentication, then demonstrate calling the protected tools.

   **Option B: Using MCP Inspector (Interactive)**

   The MCP Inspector provides an interactive web UI to explore and test your MCP server.

   **Prerequisites**: Node.js must be installed on your system.

   1. Launch the Inspector:
      ```bash
      npx -y @modelcontextprotocol/inspector
      ```

   2. In the Inspector UI (opens in your browser):
      - Enter server URL: `http://localhost:8000/mcp`
      - In the **Authentication** section's **OAuth 2.0 Flow** area, locate the **Scope** field
      - In the **Scope** field, enter: `openid profile` (these must match the `required_scopes` in your server configuration)
      - Click **Connect**
      - Your browser will open for Keycloak authentication
      - Log in with your test user credentials (e.g., `testuser` / `password123`)
      - After successful authentication, you can interactively explore available tools and test them

   **Note**: The MCP Inspector requires explicit scope configuration because it doesn't automatically request scopes. This is correct OAuth behavior - clients should explicitly request the scopes they need.

   The Inspector is particularly useful for:
   - Exploring the server's capabilities without writing code
   - Testing individual tools with custom inputs
   - Debugging authentication and authorization issues
   - Viewing request/response details
