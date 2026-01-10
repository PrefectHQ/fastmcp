"""Tests for the standalone @resource decorator.

The @resource decorator creates Resource or ResourceTemplate objects without
registering them to a server. Objects can be added explicitly via
server.add_resource() / server.add_template() or discovered by FileSystemProvider.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.resources import FunctionResource, resource
from fastmcp.resources.template import FunctionResourceTemplate


class TestResourceDecorator:
    """Tests for the @resource decorator."""

    def test_resource_requires_uri(self):
        """@resource should require a URI argument."""
        with pytest.raises(TypeError, match="requires a URI|was used incorrectly"):

            @resource  # type: ignore[arg-type]
            def get_config() -> str:
                return "{}"

    def test_resource_with_uri(self):
        """@resource("uri") should create a FunctionResource."""

        @resource("config://app")
        def get_config() -> dict:
            return {"setting": "value"}

        assert isinstance(get_config, FunctionResource)
        assert str(get_config.uri) == "config://app"

    def test_resource_with_template_uri(self):
        """@resource with template URI should create a FunctionResourceTemplate."""

        @resource("users://{user_id}/profile")
        def get_profile(user_id: str) -> dict:
            return {"id": user_id}

        assert isinstance(get_profile, FunctionResourceTemplate)
        assert get_profile.uri_template == "users://{user_id}/profile"

    def test_resource_with_function_params_becomes_template(self):
        """@resource with function params and URI params should create a template."""

        @resource("data://items/{category}")
        def get_items(category: str, limit: int = 10) -> list:
            return list(range(limit))

        assert isinstance(get_items, FunctionResourceTemplate)
        assert get_items.uri_template == "data://items/{category}"

    def test_resource_with_all_metadata(self):
        """@resource with all metadata should store it all."""

        @resource(
            "config://app",
            name="app-config",
            title="Application Config",
            description="Gets app configuration",
            mime_type="application/json",
            tags={"config"},
            meta={"custom": "value"},
        )
        def get_config() -> dict:
            return {"setting": "value"}

        assert isinstance(get_config, FunctionResource)
        assert str(get_config.uri) == "config://app"
        assert get_config.name == "app-config"
        assert get_config.title == "Application Config"
        assert get_config.description == "Gets app configuration"
        assert get_config.mime_type == "application/json"
        assert get_config.tags == {"config"}
        assert get_config.meta == {"custom": "value"}

    async def test_resource_can_be_read(self):
        """Resource created by @resource should be readable."""

        @resource("config://app")
        def get_config() -> dict:
            """Get config."""
            return {"setting": "value"}

        assert isinstance(get_config, FunctionResource)
        result = await get_config.read()
        assert result == {"setting": "value"}

    def test_resource_rejects_classmethod_decorator(self):
        """@resource should reject classmethod-decorated functions."""
        with pytest.raises(TypeError, match="classmethod"):

            class MyClass:
                @resource("config://app")  # type: ignore[arg-type]
                @classmethod
                def get_config(cls) -> str:
                    return "{}"

    async def test_resource_added_to_server(self):
        """Resource created by @resource should work when added to a server."""

        @resource("config://app")
        def get_config() -> str:
            """Get config."""
            return '{"version": "1.0"}'

        assert isinstance(get_config, FunctionResource)

        mcp = FastMCP("Test")
        mcp.add_resource(get_config)

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert any(str(r.uri) == "config://app" for r in resources)

            result = await client.read_resource("config://app")
            assert "1.0" in str(result)

    async def test_template_added_to_server(self):
        """Template created by @resource should work when added to a server."""

        @resource("users://{user_id}/profile")
        def get_profile(user_id: str) -> str:
            """Get user profile."""
            return f'{{"id": "{user_id}"}}'

        assert isinstance(get_profile, FunctionResourceTemplate)

        mcp = FastMCP("Test")
        mcp.add_template(get_profile)

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert any(t.uriTemplate == "users://{user_id}/profile" for t in templates)

            result = await client.read_resource("users://123/profile")
            assert "123" in str(result)
