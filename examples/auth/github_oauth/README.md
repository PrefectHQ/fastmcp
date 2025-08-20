# GitHub OAuth Example

Demonstrates FastMCP server protection with GitHub OAuth.

## Setup

1. Create a GitHub OAuth App:
   - Go to GitHub Settings > Developer settings > OAuth Apps
   - Set Authorization callback URL to: `http://localhost:8000/auth/callback`
   - Copy the Client ID and Client Secret

2. Set environment variables:

   ```bash
   export FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID="your-client-id"
   export FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET="your-client-secret"
   ```

3. Run the server:

   ```bash
   python server.py
   ```

4. In another terminal, run the client:

   ```bash
   python client.py
   ```

The client will open your browser for GitHub authentication.
