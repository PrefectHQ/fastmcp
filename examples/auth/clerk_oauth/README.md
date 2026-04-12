# Clerk OAuth Example

Demonstrates FastMCP server protection with Clerk OAuth.

## Setup

1. Create a Clerk OAuth Application:
   - Go to [Clerk Dashboard](https://dashboard.clerk.com/)
   - Create or select an application
   - Go to Developers > OAuth Applications
   - Create an OAuth application
   - Add Authorized redirect URI: `http://localhost:8000/auth/callback`
   - Copy the Client ID and Client Secret
   - Note your instance domain (e.g., `saving-primate-16.clerk.accounts.dev`)

2. Set environment variables:

   ```bash
   export FASTMCP_SERVER_AUTH_CLERK_DOMAIN="your-instance.clerk.accounts.dev"
   export FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID="your-clerk-client-id"
   export FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET="your-clerk-client-secret"
   ```

3. Run the server:

   ```bash
   python server.py
   ```

4. In another terminal, run the client:

   ```bash
   python client.py
   ```

The client will open your browser for Clerk authentication.
