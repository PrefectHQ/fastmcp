"""Regression tests: proxy components preserve backend ``version`` metadata.

The proxy reads ``tags`` out of the ``fastmcp`` namespace on the wire but
historically dropped ``version``, so versioned backend components arrived at
the proxy as unversioned. ``VersionFilter`` then treated them as unversioned,
which silently hid them (with ``include_unversioned=False``) or refused to
exclude them.
"""

from __future__ import annotations

from typing import Any

import mcp_types
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import NotFoundError
from fastmcp.server.context import Context
from fastmcp.server.providers.proxy import (
    ClientFactoryT,
    ProxyClient,
    ProxyPrompt,
    ProxyProvider,
    ProxyResource,
    ProxyTemplate,
    ProxyTool,
)
from fastmcp.server.transforms import VersionFilter
from fastmcp.utilities.versions import VersionSpec


def _wire_meta(version: str, tags: list[str] | None = None) -> dict[str, Any]:
    return {"fastmcp": {"tags": tags or [], "version": version}}


def _client_factory(backend: FastMCP | None = None) -> ClientFactoryT:
    server = backend or FastMCP("unused-backend")
    return lambda: ProxyClient(FastMCPTransport(server))


class TestProxyComponentFactories:
    """Each factory copies ``version`` off the raw component's wire meta."""

    def test_tool_factory_preserves_version(self):
        tool = ProxyTool.from_mcp_tool(
            _client_factory(),
            mcp_types.Tool(
                name="calc",
                input_schema={"type": "object"},
                meta=_wire_meta("2.0", ["math"]),
            ),
        )

        assert tool.version == "2.0"
        assert tool.tags == {"math"}

    def test_resource_factory_preserves_version(self):
        resource = ProxyResource.from_mcp_resource(
            _client_factory(),
            mcp_types.Resource(
                name="data",
                uri="resource://data",
                meta=_wire_meta("2.0", ["data"]),
            ),
        )

        assert resource.version == "2.0"
        assert resource.tags == {"data"}

    def test_template_factory_preserves_version(self):
        template = ProxyTemplate.from_mcp_template(
            _client_factory(),
            mcp_types.ResourceTemplate(
                name="item",
                uri_template="resource://items/{item_id}",
                meta=_wire_meta("3.0", ["items"]),
            ),
        )

        assert template.version == "3.0"
        assert template.tags == {"items"}

    def test_prompt_factory_preserves_version(self):
        prompt = ProxyPrompt.from_mcp_prompt(
            _client_factory(),
            mcp_types.Prompt(
                name="greet",
                meta=_wire_meta("4.0", ["chat"]),
            ),
        )

        assert prompt.version == "4.0"
        assert prompt.tags == {"chat"}

    def test_factories_leave_unversioned_components_unversioned(self):
        meta = {"fastmcp": {"tags": ["plain"]}}

        tool = ProxyTool.from_mcp_tool(
            _client_factory(),
            mcp_types.Tool(name="plain", input_schema={"type": "object"}, meta=meta),
        )
        resource = ProxyResource.from_mcp_resource(
            _client_factory(),
            mcp_types.Resource(name="plain", uri="resource://plain", meta=meta),
        )
        template = ProxyTemplate.from_mcp_template(
            _client_factory(),
            mcp_types.ResourceTemplate(
                name="plain", uri_template="resource://plain/{id}", meta=meta
            ),
        )
        prompt = ProxyPrompt.from_mcp_prompt(
            _client_factory(), mcp_types.Prompt(name="plain", meta=meta)
        )

        assert tool.version is None
        assert resource.version is None
        assert template.version is None
        assert prompt.version is None


class TestProxyTemplateCreateResource:
    """Resources created from a proxied template inherit its version."""

    async def test_create_resource_propagates_version(self):
        backend = FastMCP("backend")

        @backend.resource("resource://items/{item_id}", version="3.0")
        def item(item_id: str) -> str:
            return f"item-{item_id}"

        template = ProxyTemplate.from_mcp_template(
            _client_factory(backend),
            mcp_types.ResourceTemplate(
                name="item",
                uri_template="resource://items/{item_id}",
                meta=_wire_meta("3.0"),
            ),
        )

        resource = await template.create_resource(
            "resource://items/42", {"item_id": "42"}, context=Context(fastmcp=backend)
        )

        assert resource.version == "3.0"
        assert str(resource.uri) == "resource://items/42"

    async def test_create_resource_guard_ask_propagates_version(self, monkeypatch):
        backend = FastMCP("backend")

        @backend.resource("resource://items/{item_id}", version="3.0")
        def item(item_id: str) -> str:
            return f"item-{item_id}"

        async def _input_required(*args: Any, **kwargs: Any):
            return mcp_types.InputRequiredResult(request_state="opaque")

        monkeypatch.setattr(
            "fastmcp.server.providers.proxy._relay_read_resource", _input_required
        )

        template = ProxyTemplate.from_mcp_template(
            _client_factory(backend),
            mcp_types.ResourceTemplate(
                name="item",
                uri_template="resource://items/{item_id}",
                meta=_wire_meta("3.0"),
            ),
        )

        resource = await template.create_resource(
            "resource://items/42", {"item_id": "42"}, context=Context(fastmcp=backend)
        )

        assert resource.version == "3.0"


def _versioned_backend() -> FastMCP:
    backend = FastMCP("backend")

    @backend.tool(name="calc", version="2.0")
    def calc() -> str:
        return "v2"

    @backend.resource("resource://static", version="2.0")
    def static_resource() -> str:
        return "static-v2"

    @backend.resource("resource://items/{item_id}", version="3.0")
    def item(item_id: str) -> str:
        return f"item-{item_id}"

    @backend.prompt(name="greet", version="4.0")
    def greet() -> str:
        return "hello"

    return backend


def _proxy_with_filter(*, version_filter: VersionFilter) -> FastMCP:
    proxy = FastMCP("proxy")
    proxy.add_provider(ProxyProvider(_client_factory(_versioned_backend())))
    proxy.add_transform(version_filter)
    return proxy


class TestVersionFilterOverProxy:
    """End-to-end: VersionFilter sees the proxied components' versions."""

    async def test_filter_lists_and_calls_proxied_tool(self):
        proxy = _proxy_with_filter(
            version_filter=VersionFilter(version_gte="2.0", include_unversioned=False)
        )

        tools = await proxy.list_tools()
        assert [(tool.name, tool.version) for tool in tools] == [("calc", "2.0")]

        result = await proxy.call_tool("calc")
        content = result.content[0]
        assert isinstance(content, mcp_types.TextContent)
        assert content.text == "v2"

    async def test_filter_excluding_version_hides_proxied_tool(self):
        proxy = _proxy_with_filter(
            version_filter=VersionFilter(version_gte="3.0", include_unversioned=False)
        )

        assert await proxy.list_tools() == []

        with pytest.raises(NotFoundError, match="Unknown tool"):
            await proxy.call_tool("calc")

    async def test_version_lt_excludes_proxied_tool_with_default_unversioned(self):
        # The default include_unversioned=True only rescues components that
        # really are unversioned; a proxied v2 tool must still be excluded.
        proxy = _proxy_with_filter(version_filter=VersionFilter(version_lt="1.0"))

        assert await proxy.list_tools() == []

    async def test_filter_lists_and_reads_proxied_resources_templates_prompts(self):
        proxy = _proxy_with_filter(
            version_filter=VersionFilter(version_gte="2.0", include_unversioned=False)
        )

        resources = await proxy.list_resources()
        assert [(str(r.uri), r.version) for r in resources] == [
            ("resource://static", "2.0")
        ]

        templates = await proxy.list_resource_templates()
        assert [(t.uri_template, t.version) for t in templates] == [
            ("resource://items/{item_id}", "3.0")
        ]

        prompts = await proxy.list_prompts()
        assert [(p.name, p.version) for p in prompts] == [("greet", "4.0")]

        resource = await proxy.read_resource("resource://static")
        assert resource.contents[0].content == "static-v2"

        templated = await proxy.read_resource("resource://items/42")
        assert templated.contents[0].content == "item-42"

        rendered = await proxy.render_prompt("greet")
        message_content = rendered.messages[0].content
        assert isinstance(message_content, mcp_types.TextContent)
        assert message_content.text == "hello"

    async def test_filter_excluding_version_hides_proxied_resources_and_prompts(self):
        proxy = _proxy_with_filter(version_filter=VersionFilter(version_lt="1.0"))

        assert await proxy.list_resources() == []
        assert await proxy.list_resource_templates() == []
        assert await proxy.list_prompts() == []

        with pytest.raises(NotFoundError):
            await proxy.read_resource("resource://static")
        with pytest.raises(NotFoundError):
            await proxy.render_prompt("greet")


# -----------------------------------------------------------------------------
# Multi-version backends
# -----------------------------------------------------------------------------


def _multi_version_backend() -> FastMCP:
    """Backend where every component type exposes both v1.0 and v2.0.

    Its list endpoints collapse each key to the highest version and advertise
    ``fastmcp.versions=["2.0","1.0"]``, which is exactly the shape a proxy has
    to preserve.
    """
    backend = FastMCP("backend")

    @backend.tool(name="calc", version="1.0")
    def calc_v1() -> str:
        return "v1"

    @backend.tool(name="calc", version="2.0")
    def calc_v2() -> str:
        return "v2"

    @backend.resource("resource://static", version="1.0")
    def static_v1() -> str:
        return "static-v1"

    @backend.resource("resource://static", version="2.0")
    def static_v2() -> str:
        return "static-v2"

    @backend.resource("resource://items/{item_id}", version="1.0")
    def item_v1(item_id: str) -> str:
        return f"item-v1-{item_id}"

    @backend.resource("resource://items/{item_id}", version="2.0")
    def item_v2(item_id: str) -> str:
        return f"item-v2-{item_id}"

    @backend.prompt(name="greet", version="1.0")
    def greet_v1() -> str:
        return "hello-v1"

    @backend.prompt(name="greet", version="2.0")
    def greet_v2() -> str:
        return "hello-v2"

    return backend


def _proxy_of(upstream: FastMCP, *, name: str = "proxy") -> FastMCP:
    proxy = FastMCP(name)
    proxy.add_provider(ProxyProvider(_client_factory(upstream)))
    return proxy


def _advertised_versions(meta: dict[str, Any] | None) -> list[str]:
    return (meta or {}).get("fastmcp", {}).get("versions", [])


class TestMultiVersionListing:
    """A deduplicated backend component expands into one local component per
    advertised version, so version-scoped resolution works through the proxy.
    """

    async def test_proxy_lists_every_advertised_version(self):
        proxy = _proxy_of(_multi_version_backend())

        tools = await proxy.list_tools()
        assert sorted((t.name, t.version) for t in tools) == [
            ("calc", "1.0"),
            ("calc", "2.0"),
        ]

        resources = await proxy.list_resources()
        assert sorted((str(r.uri), r.version) for r in resources) == [
            ("resource://static", "1.0"),
            ("resource://static", "2.0"),
        ]

        templates = await proxy.list_resource_templates()
        assert sorted((t.uri_template, t.version) for t in templates) == [
            ("resource://items/{item_id}", "1.0"),
            ("resource://items/{item_id}", "2.0"),
        ]

        prompts = await proxy.list_prompts()
        assert sorted((p.name, p.version) for p in prompts) == [
            ("greet", "1.0"),
            ("greet", "2.0"),
        ]

    async def test_get_tool_resolves_non_highest_version(self):
        proxy = _proxy_of(_multi_version_backend())

        v1 = await proxy.get_tool("calc", VersionSpec(eq="1.0"))
        assert v1 is not None and v1.version == "1.0"

        v2 = await proxy.get_tool("calc", VersionSpec(eq="2.0"))
        assert v2 is not None and v2.version == "2.0"

        default = await proxy.get_tool("calc")
        assert default is not None and default.version == "2.0"

        assert await proxy.get_tool("calc", VersionSpec(eq="3.0")) is None

    async def test_in_process_calls_select_non_highest_version(self):
        proxy = _proxy_of(_multi_version_backend())

        tool_result = await proxy.call_tool("calc", version=VersionSpec(eq="1.0"))
        tool_content = tool_result.content[0]
        assert isinstance(tool_content, mcp_types.TextContent)
        assert tool_content.text == "v1"

        resource = await proxy.read_resource(
            "resource://static", version=VersionSpec(eq="1.0")
        )
        assert resource.contents[0].content == "static-v1"

        templated = await proxy.read_resource(
            "resource://items/7", version=VersionSpec(eq="1.0")
        )
        assert templated.contents[0].content == "item-v1-7"

        rendered = await proxy.render_prompt("greet", version=VersionSpec(eq="1.0"))
        prompt_content = rendered.messages[0].content
        assert (
            isinstance(prompt_content, mcp_types.TextContent)
            and prompt_content.text == "hello-v1"
        )

    async def test_wire_listing_preserves_versions_through_proxy_and_chain(self):
        proxy = _proxy_of(_multi_version_backend())
        chained = _proxy_of(proxy, name="chained")

        for server in (proxy, chained):
            async with Client(server) as client:
                tools = await client.list_tools()
                assert [(t.name, _advertised_versions(t.meta)) for t in tools] == [
                    ("calc", ["2.0", "1.0"])
                ]

                resources = await client.list_resources()
                assert [
                    (str(r.uri), _advertised_versions(r.meta)) for r in resources
                ] == [("resource://static", ["2.0", "1.0"])]

                templates = await client.list_resource_templates()
                assert [
                    (t.uri_template, _advertised_versions(t.meta)) for t in templates
                ] == [("resource://items/{item_id}", ["2.0", "1.0"])]

                prompts = await client.list_prompts()
                assert [(p.name, _advertised_versions(p.meta)) for p in prompts] == [
                    ("greet", ["2.0", "1.0"])
                ]

    async def test_wire_calls_route_non_highest_version_through_chain(self):
        chained = _proxy_of(_proxy_of(_multi_version_backend()), name="chained")

        async with Client(chained) as client:
            assert (await client.call_tool("calc", version="1.0")).data == "v1"
            assert (await client.call_tool("calc")).data == "v2"

            static = await client.read_resource("resource://static", version="1.0")
            assert static[0].text == "static-v1"

            item = await client.read_resource("resource://items/3", version="1.0")
            assert item[0].text == "item-v1-3"

            prompt = await client.get_prompt("greet", version="1.0")
            assert prompt.messages[0].content.text == "hello-v1"

    async def test_namespacing_applies_to_every_advertised_version(self):
        proxy = FastMCP("proxy")
        proxy.add_provider(
            ProxyProvider(_client_factory(_multi_version_backend())),
            namespace="remote",
        )

        tools = await proxy.list_tools()
        assert sorted((t.name, t.version) for t in tools) == [
            ("remote_calc", "1.0"),
            ("remote_calc", "2.0"),
        ]

        result = await proxy.call_tool("remote_calc", version=VersionSpec(eq="1.0"))
        content = result.content[0]
        assert isinstance(content, mcp_types.TextContent)
        assert content.text == "v1"


class TestMultiVersionVersionFilter:
    """``VersionFilter`` sees each advertised version, not just the highest."""

    async def test_include_unversioned_false_lists_and_calls(self):
        proxy = _proxy_of(_multi_version_backend())
        proxy.add_transform(VersionFilter(version_gte="2.0", include_unversioned=False))

        tools = await proxy.list_tools()
        assert [(t.name, t.version) for t in tools] == [("calc", "2.0")]

        resources = await proxy.list_resources()
        assert [(str(r.uri), r.version) for r in resources] == [
            ("resource://static", "2.0")
        ]

        templates = await proxy.list_resource_templates()
        assert [(t.uri_template, t.version) for t in templates] == [
            ("resource://items/{item_id}", "2.0")
        ]

        prompts = await proxy.list_prompts()
        assert [(p.name, p.version) for p in prompts] == [("greet", "2.0")]

        result = await proxy.call_tool("calc")
        content = result.content[0]
        assert isinstance(content, mcp_types.TextContent)
        assert content.text == "v2"

    async def test_filter_that_keeps_only_non_highest_still_calls_it(self):
        proxy = _proxy_of(_multi_version_backend())
        proxy.add_transform(VersionFilter(version_lt="2.0", include_unversioned=False))

        tools = await proxy.list_tools()
        assert [(t.name, t.version) for t in tools] == [("calc", "1.0")]

        # In-process: resolution picks the only visible version and the relay
        # pins it upstream even though no request _meta carries it.
        result = await proxy.call_tool("calc")
        content = result.content[0]
        assert isinstance(content, mcp_types.TextContent)
        assert content.text == "v1"

        async with Client(proxy) as client:
            assert (await client.call_tool("calc")).data == "v1"

    async def test_include_unversioned_false_still_excludes_unversioned(self):
        backend = FastMCP("backend")

        @backend.tool(name="plain")
        def plain() -> str:
            return "plain"

        @backend.tool(name="calc", version="2.0")
        def calc() -> str:
            return "v2"

        proxy = _proxy_of(backend)
        proxy.add_transform(VersionFilter(version_gte="1.0", include_unversioned=False))

        assert [(t.name, t.version) for t in await proxy.list_tools()] == [
            ("calc", "2.0")
        ]


class TestUnversionedComponentsUnchanged:
    async def test_unversioned_components_stay_unversioned_through_chain(self):
        backend = FastMCP("backend")

        @backend.tool(name="plain")
        def plain_tool() -> str:
            return "plain"

        @backend.resource("resource://plain")
        def plain_resource() -> str:
            return "plain-resource"

        @backend.resource("resource://plain/{id}")
        def plain_template(id: str) -> str:
            return f"plain-template-{id}"

        @backend.prompt(name="plain")
        def plain_prompt() -> str:
            return "plain-prompt"

        proxy = _proxy_of(backend)
        chained = _proxy_of(proxy, name="chained")

        for server in (proxy, chained):
            tools = await server.list_tools()
            assert [(t.name, t.version) for t in tools] == [("plain", None)]

            resources = await server.list_resources()
            assert [(str(r.uri), r.version) for r in resources] == [
                ("resource://plain", None)
            ]

            templates = await server.list_resource_templates()
            assert [(t.uri_template, t.version) for t in templates] == [
                ("resource://plain/{id}", None)
            ]

            prompts = await server.list_prompts()
            assert [(p.name, p.version) for p in prompts] == [("plain", None)]

            async with Client(server) as client:
                wire_tools = await client.list_tools()
                assert _advertised_versions(wire_tools[0].meta) == []
                assert "version" not in (wire_tools[0].meta or {}).get("fastmcp", {})

                assert (await client.call_tool("plain")).data == "plain"
                read = await client.read_resource("resource://plain")
                assert read[0].text == "plain-resource"
                templated = await client.read_resource("resource://plain/9")
                assert templated[0].text == "plain-template-9"
                prompt = await client.get_prompt("plain")
                assert prompt.messages[0].content.text == "plain-prompt"


class TestProxyTemplateCreateResourceMultiVersion:
    """A proxied template's ``create_resource`` reads the version it resolved."""

    async def test_create_resource_from_non_highest_template(self):
        backend = _multi_version_backend()
        proxy = _proxy_of(backend)

        template = await proxy.get_resource_template(
            "resource://items/{item_id}", VersionSpec(eq="1.0")
        )
        assert isinstance(template, ProxyTemplate)
        assert template.version == "1.0"

        resource = await template.create_resource(
            "resource://items/9",
            {"item_id": "9"},
            context=Context(fastmcp=backend),
        )
        assert resource.version == "1.0"
        assert str(resource.uri) == "resource://items/9"

        result = await resource.read()
        assert result.contents[0].content == "item-v1-9"
