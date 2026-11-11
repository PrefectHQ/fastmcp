"""One explicitly owned server lifecycle can back independently managed HTTP apps."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx2
import pytest
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.routing import Host

from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.context import Context
from fastmcp.utilities.tests import ASGIServer, asgi_server


def _auth(host: str, token: str) -> RemoteAuthProvider:
    return RemoteAuthProvider(
        token_verifier=StaticTokenVerifier(
            tokens={token: {"client_id": host, "scopes": ["read"]}},
            required_scopes=["read"],
        ),
        authorization_servers=[AnyHttpUrl(f"https://auth.{host}")],
        base_url=f"https://{host}",
    )


@pytest.mark.parametrize("first_to_stop", [0, 1])
@pytest.mark.parametrize("stateless_http", [False, True])
async def test_shared_http_lifespan(first_to_stop: int, stateless_http: bool) -> None:
    events: list[str] = []
    resource: httpx2.AsyncClient | None = None

    @asynccontextmanager
    async def resources(server: FastMCP) -> AsyncGenerator[dict[str, Any], None]:
        nonlocal resource
        async with httpx2.AsyncClient() as http:
            resource = http
            events.append("start")
            try:
                yield {"http": http, "calls": 0}
            finally:
                events.append("stop")

    default_auth = _auth("default.example.com", "default-token")
    server = FastMCP("shared", lifespan=resources, auth=default_auth)

    @server.tool
    def use_resource(ctx: Context) -> dict[str, bool | int]:
        assert ctx.request_context is not None
        state = ctx.request_context.lifespan_context
        assert state["http"] is resource
        state["calls"] += 1
        return {"open": not state["http"].is_closed, "calls": state["calls"]}

    hosts = ["old.example.com", "new.example.com"]
    tokens = ["old-token", "new-token"]
    providers = [_auth(host, token) for host, token in zip(hosts, tokens, strict=True)]
    apps = [
        server.http_app(auth=auth, stateless_http=stateless_http) for auth in providers
    ]
    router = Starlette(
        routes=[Host(host, app=app) for host, app in zip(hosts, apps, strict=True)]
    )
    running = [
        ASGIServer(url=f"https://{host}/mcp", app=router, transport_type="http")
        for host in hosts
    ]
    ready = [asyncio.Event(), asyncio.Event()]
    stop = [asyncio.Event(), asyncio.Event()]

    async def serve(index: int) -> None:
        app = apps[index]
        async with app.lifespan(app):
            ready[index].set()
            await stop[index].wait()
        events.append(f"app-{index}-stop")

    assert events == []
    async with server.lifespan_context():
        assert events == ["start"]
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(serve(index)) for index in range(2)]
            await asyncio.gather(*(event.wait() for event in ready))

            for index, host in enumerate(hosts):
                async with running[index].http_client() as http:
                    metadata = await http.get(
                        "/.well-known/oauth-protected-resource/mcp"
                    )
                    assert metadata.status_code == 200
                    assert metadata.json()["resource"] == f"https://{host}/mcp"
                    assert metadata.json()["authorization_servers"] == [
                        f"https://auth.{host}/"
                    ]

                    missing = await http.post("/mcp")
                    assert missing.status_code == 401
                    assert (
                        f'resource_metadata="https://{host}'
                        '/.well-known/oauth-protected-resource/mcp"'
                    ) in missing.headers["www-authenticate"]
                    wrong = await http.post(
                        "/mcp",
                        headers={"Authorization": f"Bearer {tokens[1 - index]}"},
                    )
                    assert wrong.status_code == 401

                async with running[index].client(auth=tokens[index]) as client:
                    result = await client.call_tool("use_resource")
                    assert result.data == {"open": True, "calls": index + 1}

            assert server.auth is default_auth
            assert events == ["start"]
            stop[first_to_stop].set()
            await tasks[first_to_stop]
            assert events == ["start", f"app-{first_to_stop}-stop"]
            assert resource is not None and not resource.is_closed

            remaining = 1 - first_to_stop
            async with running[remaining].client(auth=tokens[remaining]) as client:
                result = await client.call_tool("use_resource")
                assert result.data == {"open": True, "calls": 3}
            stop[remaining].set()
            await tasks[remaining]

        assert events == [
            "start",
            f"app-{first_to_stop}-stop",
            f"app-{remaining}-stop",
        ]
        assert not resource.is_closed

    assert events[-1] == "stop"
    assert events.count("start") == events.count("stop") == 1
    assert resource.is_closed


@pytest.mark.parametrize("transport", ["http", "streamable-http", "sse"])
@pytest.mark.parametrize("configuration", ["inherit", "override", "disabled"])
async def test_http_app_auth_configuration(
    transport: Literal["http", "streamable-http", "sse"],
    configuration: str,
) -> None:
    default_auth = _auth("default.example.com", "default-token")
    override_auth = _auth("override.example.com", "override-token")
    server = FastMCP("auth-config", auth=default_auth)

    @server.tool
    def greet() -> str:
        return "hello"

    options: dict[str, Any] = {}
    expected_auth: RemoteAuthProvider | None = default_auth
    token: str | None = "default-token"
    if configuration == "override":
        options["auth"] = expected_auth = override_auth
        token = "override-token"
    elif configuration == "disabled":
        options["auth"] = expected_auth = None
        token = None

    async with asgi_server(server, transport=transport, **options) as running:
        assert isinstance(running.app, Starlette)
        assert running.app.state.fastmcp_auth is expected_auth
        async with running.client(auth=token) as client:
            result = await client.call_tool("greet")
            assert result.data == "hello"

        if expected_auth is not None:
            path = "/sse" if transport == "sse" else "/mcp"
            async with running.http_client() as http:
                response = await http.get(path)
                assert response.status_code == 401
                if configuration == "override":
                    response = await http.get(
                        path, headers={"Authorization": "Bearer default-token"}
                    )
                    assert response.status_code == 401

    assert server.auth is default_auth
    assert server.http_app().state.fastmcp_auth is default_auth
