"""Declared OpenAPI cookie parameters and client session cookies travel together."""

import httpx2

from fastmcp import Client, FastMCP


async def test_tool_merges_cookie_parameters_with_applicable_client_cookies():
    seen: list[str] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers["cookie"])
        return httpx2.Response(200, json={})

    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Items", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "items",
                    "parameters": [
                        {"name": "lang", "in": "cookie", "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    async with httpx2.AsyncClient(
        base_url="https://example.test",
        cookies={"lang": "de"},
        transport=httpx2.MockTransport(capture),
    ) as client:
        client.cookies.set("session", "abc", domain="example.test", path="/items")
        client.cookies.set("elsewhere", "private", domain="other.test")
        client.cookies.set("admin", "private", domain="example.test", path="/admin")
        async with Client(FastMCP.from_openapi(spec, client=client)) as mcp:
            await mcp.call_tool("items", {"lang": "en"})
            await mcp.call_tool("items", {})

    assert sorted(seen[0].split("; ")) == ["lang=en", "session=abc"]
    assert sorted(seen[1].split("; ")) == ["lang=de", "session=abc"]
