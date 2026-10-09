"""Session-scoped features on MCP 2026-07-28 connections.

A 2026-07-28 connection has no session, so `FileUpload` and per-session
visibility scope their data to the authenticated user, or to the server
process on stdio and in-memory transports. An unauthenticated HTTP request
carries no client identity and is rejected rather than losing data.
"""

import base64

import pytest

from fastmcp import Client, Context, FastMCP
from fastmcp.apps.file_upload import FileUpload
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.utilities.tests import run_server_async

UPLOAD = {
    "name": "a.txt",
    "size": 5,
    "type": "text/plain",
    "data": base64.b64encode(b"hello").decode(),
}


def build_server(auth: StaticTokenVerifier | None = None) -> FastMCP:
    server = FastMCP("scope", auth=auth)
    server.add_provider(FileUpload())

    @server.tool
    async def hide_secret(ctx: Context) -> str:
        await ctx.disable_components(names={"secret"})
        return "hidden"

    @server.tool
    def secret() -> str:
        return "s"

    return server


async def upload_tool_names(client: Client) -> tuple[str, str]:
    names = {t.name for t in await client.list_tools()}
    store = next(n for n in names if n.endswith("store_files"))
    list_files = next(n for n in names if n.endswith("list_files"))
    return store, list_files


async def listed_files(client: Client, list_files: str) -> list[dict]:
    result = await client.call_tool(list_files, {})
    assert result.structured_content is not None
    return result.structured_content["result"]


async def secret_visible(client: Client) -> bool:
    return "secret" in {t.name for t in await client.list_tools()}


class TestInMemory:
    async def test_uploaded_files_persist_across_calls(self):
        async with Client(build_server()) as client:
            store, list_files = await upload_tool_names(client)
            await client.call_tool(store, {"files": [UPLOAD]})

            files = await listed_files(client, list_files)

        assert [f["name"] for f in files] == ["a.txt"]

    async def test_disabled_components_stay_hidden(self):
        async with Client(build_server()) as client:
            await client.call_tool("hide_secret", {})

            assert not await secret_visible(client)


class TestUnauthenticatedHttp:
    async def test_upload_is_rejected(self):
        async with run_server_async(build_server()) as url:
            async with Client(url) as client:
                store, _ = await upload_tool_names(client)

                with pytest.raises(ToolError, match="no client identity"):
                    await client.call_tool(store, {"files": [UPLOAD]})

    async def test_disable_components_is_rejected(self):
        async with run_server_async(build_server()) as url:
            async with Client(url) as client:
                with pytest.raises(ToolError, match="no client identity"):
                    await client.call_tool("hide_secret", {})

                assert await secret_visible(client)

    async def test_legacy_session_still_scopes_by_session(self):
        async with run_server_async(build_server()) as url:
            async with Client(url, mode="legacy") as client:
                store, list_files = await upload_tool_names(client)
                await client.call_tool(store, {"files": [UPLOAD]})
                await client.call_tool("hide_secret", {})

                files = await listed_files(client, list_files)
                visible = await secret_visible(client)

        assert [f["name"] for f in files] == ["a.txt"]
        assert not visible


class TestAuthenticatedHttp:
    @pytest.fixture
    def verifier(self) -> StaticTokenVerifier:
        return StaticTokenVerifier(
            {
                "token-a": {"client_id": "c", "scopes": [], "sub": "user-a"},
                "token-b": {"client_id": "c", "scopes": [], "sub": "user-b"},
            }
        )

    async def test_uploads_are_scoped_to_the_user(self, verifier: StaticTokenVerifier):
        async with run_server_async(build_server(auth=verifier)) as url:
            async with Client(url, auth="token-a") as user_a:
                store, list_files = await upload_tool_names(user_a)
                await user_a.call_tool(store, {"files": [UPLOAD]})
                files_a = await listed_files(user_a, list_files)

            async with Client(url, auth="token-b") as user_b:
                files_b = await listed_files(user_b, list_files)

        assert [f["name"] for f in files_a] == ["a.txt"]
        assert files_b == []

    async def test_visibility_rules_are_scoped_to_the_user(
        self, verifier: StaticTokenVerifier
    ):
        async with run_server_async(build_server(auth=verifier)) as url:
            async with Client(url, auth="token-a") as user_a:
                await user_a.call_tool("hide_secret", {})
                visible_to_a = await secret_visible(user_a)

            async with Client(url, auth="token-b") as user_b:
                visible_to_b = await secret_visible(user_b)

        assert not visible_to_a
        assert visible_to_b
