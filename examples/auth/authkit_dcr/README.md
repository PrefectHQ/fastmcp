# AuthKit DCR Example

Demonstrates FastMCP server protection with AuthKit Dynamic Client Registration.

## Setup

1. Set your AuthKit domain:

   ```bash
   export FASTMCP_SERVER_AUTH_AUTHKITPROVIDER_AUTHKIT_DOMAIN="https://your-app.authkit.app"
   ```

2. Run the server:

   ```bash
   python server.py
   ```

3. In another terminal, run the client:

   ```bash
   python client.py
   ```

The client will open your browser for AuthKit authentication.
