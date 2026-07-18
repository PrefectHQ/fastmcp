import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.huggingface import HuggingFaceProvider
from fastmcp.server.dependencies import get_access_token

auth_provider = HuggingFaceProvider(
    # Your Hugging Face OAuth app client ID
    client_id=os.getenv("FASTMCP_SERVER_AUTH_HF_CLIENT_ID") or "",
    # Your Hugging Face OAuth app client secret
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_HF_CLIENT_SECRET") or "",
    # Must match your OAuth configuration
    base_url="http://localhost:8000",
    # Supply jwt_signing_key instead of client_secret for public applications
    # jwt_signing_key="replace-with-a-secure-secret"
)

mcp = FastMCP(name="Hugging Face Secured App", auth=auth_provider)


# Add a tool to test authentication
@mcp.tool
async def get_user_info() -> dict:
    """Returns information about the authenticated Hugging Face user."""
    token = get_access_token()
    if token is None:
        return {"error": "Not authenticated"}
    return {
        "subject": token.claims.get("sub"),
        "username": token.claims.get("preferred_username"),
        "profile": token.claims.get("profile"),
    }


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
