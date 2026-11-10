# Hugging FAce OAuth Example

Demonstrates FastMCP server protection with Hugging Face OAuth.

## Setup

1. Create a Hugging Face OAuth App:
   - Go to Hugging Face Settings > Connected Apps > Create App (`https://huggingface.co/settings/applications/new`)
   - Set Authorization callback URL to: `http://localhost:8000/auth/callback`
   - Copy the Client ID and Client Secret

2. Set environment variables:

   ```bash
   export FASTMCP_SERVER_AUTH_HF_CLIENT_ID="your-client-id"
   export FASTMCP_SERVER_AUTH_HF_CLIENT_SECRET="your-client-secret"
   ```

3. Run the server:

   ```bash
   python server.py
   ```

4. In another terminal, run the client:

   ```bash
   python client.py
   ```

The client will open your browser for Hugging Face authentication.
