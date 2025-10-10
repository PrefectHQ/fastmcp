"""AWS Cognito OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with AWS Cognito.

Required environment variables:
- FASTMCP_SERVER_AUTH_AWS_COGNITO_USER_POOL_ID: Your AWS Cognito User Pool ID
- FASTMCP_SERVER_AUTH_AWS_COGNITO_AWS_REGION: Your AWS region (optional, defaults to eu-central-1)
- FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_ID: Your Cognito app client ID
- FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_SECRET: Your Cognito app client secret

To run:
    python server.py
"""

import logging
import os

from dotenv import load_dotenv

from fastmcp import FastMCP
from fastmcp.server.auth.providers.aws import AWSCognitoProvider
from fastmcp.server.dependencies import get_access_token

logging.basicConfig(level=logging.DEBUG)

load_dotenv(".env", override=True)

auth = AWSCognitoProvider(
    user_pool_id=os.getenv("FASTMCP_SERVER_AUTH_AWS_COGNITO_USER_POOL_ID") or "",
    aws_region=os.getenv("FASTMCP_SERVER_AUTH_AWS_COGNITO_AWS_REGION")
    or "eu-central-1",
    client_id=os.getenv("FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_SECRET") or "",
    base_url="http://localhost:8000",
    # redirect_path="/custom/callback"
)

mcp = FastMCP("AWS Cognito OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


@mcp.tool
async def get_access_token_claims() -> dict:
    """Get the authenticated user's access token claims."""
    token = get_access_token()
    return {
        "sub": token.claims.get("sub"),
        "username": token.claims.get("username"),
        "cognito:groups": token.claims.get("cognito:groups", []),
    }


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
