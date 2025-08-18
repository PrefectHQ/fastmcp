import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider

auth = GoogleProvider(
    client_id=os.getenv("FASTMCP_TEST_AUTH_GOOGLE_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_TEST_AUTH_GOOGLE_CLIENT_SECRET") or "",
    base_url="http://localhost:8000",
    required_scopes=["openid"],
)

mcp = FastMCP("Google OAuth Test Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
