"""OBO dependencies use the authentication provider of the serving HTTP app."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, MultiAuth
from fastmcp.server.auth.providers.azure import AzureProvider, EntraOBOToken
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.utilities.tests import asgi_server

pytest.importorskip("azure.identity.aio")


def _auth(name: str) -> MultiAuth:
    provider = AzureProvider(
        client_id=f"{name}-client",
        client_secret=f"{name}-secret",
        tenant_id=f"{name}-tenant",
        required_scopes=["read"],
        additional_authorize_scopes=["https://graph.microsoft.com/User.Read"],
        base_url=f"https://{name}.example.com",
        client_storage=MemoryStore(),
        jwt_signing_key=f"{name}-signing-key",
    )
    return MultiAuth(
        server=provider,
        verifiers={
            "static": StaticTokenVerifier(
                tokens={f"{name}-token": {"client_id": name, "scopes": ["read"]}}
            )
        },
    )


@pytest.fixture
def credential_factory() -> Iterator[MagicMock]:
    def credential(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            get_token=AsyncMock(
                return_value=SimpleNamespace(token=kwargs["client_id"])
            ),
            close=AsyncMock(),
        )

    with patch(
        "azure.identity.aio.OnBehalfOfCredential", side_effect=credential
    ) as factory:
        yield factory


@pytest.mark.parametrize(
    "configuration", ["inherited", "app-only", "different-default"]
)
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_obo_uses_serving_app_auth(
    configuration: str,
    transport: Literal["http", "sse"],
    credential_factory: MagicMock,
) -> None:
    app_auth = _auth("app")
    default_auth: MultiAuth | None = None
    if configuration == "inherited":
        default_auth = app_auth
    elif configuration == "different-default":
        default_auth = _auth("default")
    server = FastMCP("obo-app-auth", auth=default_auth)

    @server.tool
    async def downstream(
        token: str = EntraOBOToken(["https://graph.microsoft.com/User.Read"]),
    ) -> str:
        return token

    options: dict[str, Any] = {} if configuration == "inherited" else {"auth": app_auth}
    async with asgi_server(server, transport=transport, **options) as running:
        async with running.client(auth="app-token") as client:
            result = await client.call_tool("downstream")
            assert result.data == "app-client"

    credential_factory.assert_called_once_with(
        tenant_id="app-tenant",
        client_id="app-client",
        client_secret="app-secret",
        user_assertion="app-token",
        authority="https://login.microsoftonline.com",
    )
    assert server.auth is default_auth


@pytest.mark.parametrize("http_auth_disabled", [False, True])
async def test_obo_server_auth_fallback_is_only_for_non_http_requests(
    http_auth_disabled: bool,
    credential_factory: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    default_auth = _auth("default")
    server = FastMCP("obo-fallback", auth=default_auth)

    @server.tool
    async def downstream(
        token: str = EntraOBOToken(["https://graph.microsoft.com/User.Read"]),
    ) -> str:
        return token

    access_token = AccessToken(token="user-token", client_id="user", scopes=["read"])
    marker = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        if http_auth_disabled:
            async with asgi_server(server, auth=None) as running:
                async with running.client() as client:
                    with pytest.raises(
                        ToolError, match="Failed to resolve dependency 'token'"
                    ):
                        await client.call_tool("downstream")
            assert "Current provider: NoneType" in caplog.text
            credential_factory.assert_not_called()
        else:
            async with Client(server) as client:
                result = await client.call_tool("downstream")
                assert result.data == "default-client"
            credential_factory.assert_called_once_with(
                tenant_id="default-tenant",
                client_id="default-client",
                client_secret="default-secret",
                user_assertion="user-token",
                authority="https://login.microsoftonline.com",
            )
    finally:
        auth_context_var.reset(marker)
    assert server.auth is default_auth
