import json

import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import (
    ClientTransport,
    SSETransport,
    StreamableHttpTransport,
)
from fastmcp.server import create_proxy
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.providers.proxy import ProxyClient
from fastmcp.utilities.tests import run_server_async


@pytest.mark.parametrize("forward", [None, True, False])
@pytest.mark.parametrize("kind", ["http", "sse", "single", "multiple"])
async def test_proxy_header_forwarding_can_be_selected(kind: str, forward: bool | None):
    backend = FastMCP("Backend")

    @backend.tool
    def headers() -> dict[str, str]:
        return get_http_headers(include_all=True)

    @backend.resource("headers://fixed")
    def resource_headers() -> str:
        return json.dumps(get_http_headers(include_all=True))

    @backend.resource("headers://{name}")
    def template_headers(name: str) -> str:
        return json.dumps(get_http_headers(include_all=True))

    @backend.prompt
    def prompt_headers() -> str:
        return json.dumps(get_http_headers(include_all=True))

    backend_transport = "sse" if kind == "sse" else "http"
    async with run_server_async(backend, transport=backend_transport) as url:
        configured = {"x-backend": "configured", "x-shared": "backend"}
        if kind == "http":
            target = StreamableHttpTransport(url, headers=configured)
        elif kind == "sse":
            target = SSETransport(url, headers=configured)
        else:
            names = ["first", "second"] if kind == "multiple" else ["first"]
            target = {
                "mcpServers": {
                    name: {"url": url, "headers": configured} for name in names
                }
            }
        proxy_client = (
            ProxyClient[ClientTransport](target)
            if forward is None
            else ProxyClient[ClientTransport](target, forward_incoming_headers=forward)
        )
        proxy = create_proxy(proxy_client)
        async with run_server_async(proxy) as proxy_url:
            transport = StreamableHttpTransport(
                proxy_url,
                headers={
                    "authorization": "Bearer caller",
                    "x-caller": "inbound",
                    "x-shared": "caller",
                    "cookie": "local=value",
                },
            )
            async with Client(transport, mode="legacy") as client:
                tools = await client.list_tools()
                assert len(tools) == (2 if kind == "multiple" else 1)
                received_headers = []
                for tool in tools:
                    result = await client.call_tool(tool.name, {})
                    received_headers.append(result.data)
                resources = await client.list_resources()
                for resource in resources:
                    result = await client.read_resource(resource.uri)
                    received_headers.append(json.loads(result[0].text))
                templates = await client.list_resource_templates()
                for template in templates:
                    uri = template.uri_template.replace("{name}", "daily")
                    result = await client.read_resource(uri)
                    received_headers.append(json.loads(result[0].text))
                prompts = await client.list_prompts()
                for prompt in prompts:
                    result = await client.get_prompt(prompt.name)
                    received_headers.append(json.loads(result.messages[0].content.text))
                for received in received_headers:
                    assert received["x-backend"] == "configured"
                    assert received["x-shared"] == "backend"
                    assert "cookie" not in received
                    if forward is not False:
                        assert received["authorization"] == "Bearer caller"
                        assert received["x-caller"] == "inbound"
                    else:
                        assert "authorization" not in received
                        assert "x-caller" not in received


def test_proxy_copies_keep_independent_forwarding_options():
    transport = StreamableHttpTransport("http://backend/mcp")
    forwarding = ProxyClient(transport)
    configured_only = ProxyClient(transport, forward_incoming_headers=False)

    assert forwarding._transport_options is not None
    assert forwarding._transport_options.forward_incoming_headers is True
    assert configured_only._transport_options is not None
    assert (
        configured_only.new()._transport_options is configured_only._transport_options
    )
    assert configured_only._transport_options.forward_incoming_headers is False
    assert Client(transport)._transport_options is None
