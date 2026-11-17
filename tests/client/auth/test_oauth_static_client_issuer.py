"""Pre-registered OAuth credentials must stay with their issuer."""

import json

import httpx2
import pytest
from mcp.client.auth import OAuthFlowError

from fastmcp.client.auth import OAuth

MCP_URL = "https://mcp.victim.example/mcp"
PRM_URL = "https://mcp.victim.example/.well-known/oauth-protected-resource/mcp"
LEGIT_ISSUER = "https://idp.example.com"


def unauthorized(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(
        401,
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{PRM_URL}"'},
        request=request,
    )


def resource_metadata(request: httpx2.Request, auth_server: str) -> httpx2.Response:
    body = {"resource": MCP_URL, "authorization_servers": [auth_server]}
    return httpx2.Response(200, content=json.dumps(body), request=request)


async def drive_until_metadata(oauth: OAuth, auth_server: str) -> list[str]:
    """Run the flow through resource metadata discovery; return requested URLs."""
    flow = oauth.async_auth_flow(httpx2.Request("POST", MCP_URL))
    requested: list[str] = []
    request = await flow.__anext__()
    requested.append(str(request.url))
    request = await flow.asend(unauthorized(request))
    requested.append(str(request.url))
    request = await flow.asend(resource_metadata(request, auth_server))
    requested.append(str(request.url))
    await flow.aclose()
    return requested


async def test_secret_is_not_sent_to_a_different_authorization_server():
    oauth = OAuth(
        mcp_url=MCP_URL,
        client_id="pre-registered",
        client_secret="S3CRET",
        issuer=LEGIT_ISSUER,
    )
    with pytest.raises(OAuthFlowError, match="evil.example"):
        await drive_until_metadata(oauth, "https://evil.example")


async def test_matching_issuer_continues_to_its_metadata():
    oauth = OAuth(
        mcp_url=MCP_URL,
        client_id="pre-registered",
        client_secret="S3CRET",
        issuer=LEGIT_ISSUER,
    )
    requested = await drive_until_metadata(oauth, LEGIT_ISSUER)
    assert requested[-1].startswith(f"{LEGIT_ISSUER}/.well-known/")


async def test_issuer_is_recorded_on_static_client_info():
    oauth = OAuth(
        mcp_url=MCP_URL,
        client_id="pre-registered",
        client_secret="S3CRET",
        issuer=LEGIT_ISSUER,
    )
    assert oauth._static_client_info is not None
    assert oauth._static_client_info.issuer == LEGIT_ISSUER


async def test_without_issuer_behavior_is_unchanged():
    oauth = OAuth(mcp_url=MCP_URL, client_id="pre-registered", client_secret="S3CRET")
    requested = await drive_until_metadata(oauth, "https://other.example")
    assert requested[-1].startswith("https://other.example/.well-known/")
