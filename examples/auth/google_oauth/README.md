# Google OAuth Example

Demonstrates FastMCP server protection with Google OAuth.

## Setup

1. Create a Google OAuth 2.0 Client:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create or select a project
   - Go to APIs & Services > Credentials
   - Create OAuth 2.0 Client ID (Web application)
   - Add Authorized redirect URI: `http://localhost:8000/auth/callback`
   - Copy the Client ID and Client Secret

2. Set environment variables:

   ```bash
   export FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
   export FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET="your-client-secret"
   ```

3. Run the server:

   ```bash
   python server.py
   ```

4. In another terminal, run the client:

   ```bash
   python client.py
   ```

The client will open your browser for Google authentication.
