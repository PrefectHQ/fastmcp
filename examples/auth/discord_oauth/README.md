# Discord OAuth Example

Demonstrates FastMCP server protection with Discord OAuth.

## Setup

1. Create a Discord OAuth App:
   - Go to https://discord.com/developers/applications
   - Click "New Application" and give it a name
   - Go to OAuth2 in the left sidebar
   - Add a Redirect URL: `http://localhost:8000/auth/callback`
   - Copy the Client ID and Client Secret

2. Set environment variables:

   ```bash
   export FASTMCP_SERVER_AUTH_DISCORD_CLIENT_ID="your-client-id"
   export FASTMCP_SERVER_AUTH_DISCORD_CLIENT_SECRET="your-client-secret"
   ```

3. Run the server:

   ```bash
   python server.py
   ```

4. In another terminal, run the client:

   ```bash
   python client.py
   ```

The client will open your browser for Discord authentication.
