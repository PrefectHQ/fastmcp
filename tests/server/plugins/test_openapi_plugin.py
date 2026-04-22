"""Tests for the OpenAPI plugin wrapper.

Transform/provider behavior is covered by the existing OpenAPIProvider
tests in `tests/server/providers/openapi/`. This file only covers
plugin-layer concerns — config validation, dict→RouteMap conversion,
spec_path loading, and the escape-hatch wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from fastmcp import Client, FastMCP
from fastmcp.server.plugins.openapi import MCPType, OpenAPI, OpenAPIConfig, RouteMap
from fastmcp.server.plugins.openapi.plugin import RouteMapDict
from fastmcp.server.plugins.openapi.provider import OpenAPIProvider

PETSTORE_SPEC: dict = {
    "openapi": "3.0.0",
    "info": {"title": "Petstore", "version": "1.0"},
    "servers": [{"url": "https://petstore.example.com"}],
    "paths": {
        "/pets": {
            "get": {
                "operationId": "list_pets",
                "responses": {"200": {"description": "ok"}},
            },
            "post": {
                "operationId": "create_pet",
                "responses": {"201": {"description": "created"}},
            },
        },
        "/pets/{id}": {
            "get": {
                "operationId": "get_pet",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


class TestOpenAPIConfig:
    def test_config_generic_binding(self):
        assert OpenAPI._config_cls is OpenAPIConfig

    def test_default_config_instantiable(self):
        """Defaults must pass the plugin framework's instantiate-with-no-args
        contract. The spec/spec_path check fires at providers() time, not
        at Config construction."""
        assert OpenAPIConfig()  # must not raise

    def test_unknown_config_key_rejected(self):
        with pytest.raises((ValidationError, Exception), match="forbid|extra"):
            OpenAPIConfig(not_a_real_option=True)  # ty: ignore[unknown-argument]

    def test_meta_name_is_single_word(self):
        """'openapi' is one technical term — explicit meta override
        prevents the kebab auto-deriver from producing 'open-api'."""
        assert OpenAPI.meta.name == "openapi"
        assert OpenAPI.meta.version is None


class TestSpecLoading:
    async def test_inline_spec_builds_provider(self):
        plugin = OpenAPI(OpenAPIConfig(spec=PETSTORE_SPEC))
        mcp = FastMCP("petstore", plugins=[plugin])

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        assert {"list_pets", "create_pet", "get_pet"}.issubset(names)

    async def test_spec_path_loads_from_disk(self, tmp_path: Path):
        spec_file = tmp_path / "petstore.json"
        spec_file.write_text(json.dumps(PETSTORE_SPEC))

        plugin = OpenAPI(OpenAPIConfig(spec_path=str(spec_file)))
        mcp = FastMCP("petstore", plugins=[plugin])

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        assert {"list_pets", "create_pet", "get_pet"}.issubset(names)

    async def test_spec_path_loads_utf8_regardless_of_locale(self, tmp_path: Path):
        """Spec files must load as UTF-8, not via the process locale.
        Otherwise a spec with non-ASCII descriptions (German umlauts,
        Japanese, fancy quotes, etc.) fails on non-UTF-8 systems like
        Windows cp1252 — see PR #4015 review thread."""
        spec_with_unicode = {
            **PETSTORE_SPEC,
            "info": {"title": "Pëtstöre — 宠物商店", "version": "1.0"},
        }
        spec_file = tmp_path / "petstore-unicode.json"
        spec_file.write_text(
            json.dumps(spec_with_unicode, ensure_ascii=False),
            encoding="utf-8",
        )

        plugin = OpenAPI(OpenAPIConfig(spec_path=str(spec_file)))
        providers = plugin.providers()
        assert isinstance(providers[0], OpenAPIProvider)

    def test_missing_spec_fails_at_build_time(self):
        plugin = OpenAPI(OpenAPIConfig())
        with pytest.raises(ValueError, match="spec.*spec_path"):
            plugin.providers()

    def test_both_spec_and_spec_path_rejected(self, tmp_path: Path):
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(PETSTORE_SPEC))

        plugin = OpenAPI(OpenAPIConfig(spec=PETSTORE_SPEC, spec_path=str(spec_file)))
        with pytest.raises(ValueError, match="exactly one"):
            plugin.providers()


class TestRouteMapping:
    def test_route_maps_dict_form_converts_to_typed(self):
        plugin = OpenAPI(
            OpenAPIConfig(
                spec=PETSTORE_SPEC,
                route_maps=[
                    RouteMapDict(
                        mcp_type="RESOURCE", methods=["GET"], pattern=r"^/pets$"
                    ),
                ],
            )
        )
        providers = plugin.providers()
        assert isinstance(providers[0], OpenAPIProvider)
        # The GET /pets route should have become a resource, not a tool.

    async def test_list_pets_maps_to_resource_via_config(self):
        plugin = OpenAPI(
            OpenAPIConfig(
                spec=PETSTORE_SPEC,
                route_maps=[
                    RouteMapDict(
                        mcp_type="RESOURCE", methods=["GET"], pattern=r"^/pets$"
                    ),
                ],
            )
        )
        mcp = FastMCP("petstore", plugins=[plugin])

        async with Client(mcp) as c:
            tools = {t.name for t in await c.list_tools()}
            resources = {str(r.uri) for r in await c.list_resources()}

        assert "list_pets" not in tools
        assert any("list_pets" in uri or "/pets" in uri for uri in resources)

    def test_typed_route_maps_override_dict_config(self):
        """When users pass typed `route_maps=` to `__init__`, that beats
        the dict form in Config — advanced users shouldn't be shadowed
        by an empty default."""
        plugin = OpenAPI(
            OpenAPIConfig(spec=PETSTORE_SPEC),
            route_maps=[RouteMap(mcp_type=MCPType.EXCLUDE, pattern=r".*")],
        )
        providers = plugin.providers()
        provider = providers[0]
        # Every route was excluded → provider has no tools/resources.
        assert isinstance(provider, OpenAPIProvider)


class TestDefaultClient:
    async def test_plugin_built_client_is_closed_on_provider_lifespan_exit(self):
        """When the plugin builds its own httpx client (user didn't pass
        `client=`), the provider's lifespan must still close it on
        shutdown. A leaked client was bug noted on PR #4015."""
        plugin = OpenAPI(OpenAPIConfig(spec=PETSTORE_SPEC))
        provider = plugin.providers()[0]
        assert isinstance(provider, OpenAPIProvider)
        client = provider._client

        assert not client.is_closed
        async with provider.lifespan():
            pass
        assert client.is_closed

    async def test_server_variable_defaults_are_substituted(self):
        """Spec servers with `{variable}` placeholders must be resolved
        using `servers[0].variables[name].default` before going to the
        httpx client — otherwise the literal template leaks into every
        request URL."""
        templated_spec = {
            **PETSTORE_SPEC,
            "servers": [
                {
                    "url": "https://{region}.api.example.com",
                    "variables": {"region": {"default": "us-east"}},
                }
            ],
        }
        plugin = OpenAPI(OpenAPIConfig(spec=templated_spec))
        provider = plugin.providers()[0]
        assert isinstance(provider, OpenAPIProvider)
        assert str(provider._client.base_url) == "https://us-east.api.example.com"


class TestEscapeHatches:
    async def test_custom_client_is_used(self):
        """Passing `client=` bypasses the auto-derived httpx client."""
        client = httpx.AsyncClient(base_url="https://override.example.com")
        plugin = OpenAPI(OpenAPIConfig(spec=PETSTORE_SPEC), client=client)
        providers = plugin.providers()
        assert isinstance(providers[0], OpenAPIProvider)
        # Access the provider's client through the known private attr.
        # This is an implementation check — acceptable in a test.
        assert providers[0]._client is client
        await client.aclose()


class TestDeprecationShim:
    """The old `fastmcp.server.providers.openapi` location now shims
    back to the new plugin package. Top-level import is silent (so
    unrelated code touching `fastmcp.server.providers` doesn't spray
    warnings), but leaf submodules emit a `FastMCPDeprecationWarning`."""

    async def test_top_level_old_path_is_silent_and_functional(self):
        """Still-common `from fastmcp.server.providers.openapi import
        OpenAPIProvider` keeps working without emitting a warning."""
        import warnings

        from fastmcp.exceptions import FastMCPDeprecationWarning

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            from fastmcp.server.providers.openapi import (
                OpenAPIProvider as LegacyProvider,
            )

        fastmcp_warns = [
            w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
        ]
        assert not fastmcp_warns

        client = httpx.AsyncClient(base_url="https://petstore.example.com")
        provider = LegacyProvider(openapi_spec=PETSTORE_SPEC, client=client)
        mcp = FastMCP("petstore", providers=[provider])

        async with Client(mcp) as c:
            tools = {t.name for t in await c.list_tools()}

        assert {"list_pets", "create_pet", "get_pet"}.issubset(tools)
        assert LegacyProvider is OpenAPIProvider
        await client.aclose()

    def test_leaf_submodule_import_emits_deprecation_warning(self):
        import importlib
        import sys
        import warnings

        from fastmcp.exceptions import FastMCPDeprecationWarning

        sys.modules.pop("fastmcp.server.providers.openapi.provider", None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("fastmcp.server.providers.openapi.provider")

        fastmcp_warns = [
            w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
        ]
        assert any("plugins.openapi" in str(w.message) for w in fastmcp_warns), (
            f"expected FastMCPDeprecationWarning pointing at plugins.openapi, "
            f"got {[(w.category.__name__, str(w.message)) for w in caught]}"
        )
