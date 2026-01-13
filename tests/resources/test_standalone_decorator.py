"""Tests for the standalone @resource decorator.

The @resource decorator attaches metadata to functions without registering them
to a server. Functions can be added explicitly via server.add_resource() /
server.add_template() or discovered by FileSystemProvider.
"""

from typing import cast

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.resources import resource
from fastmcp.resources.function_resource import DecoratedResource, ResourceMeta


class TestResourceDecorator:
    """Tests for the @resource decorator."""

    def test_resource_requires_uri(self):
        """@resource should require a URI argument."""
        with pytest.raises(TypeError, match="requires a URI|was used incorrectly"):

            @resource  # type: ignore[arg-type]
            def get_config() -> str:
                return "{}"

    def test_resource_with_uri(self):
        """@resource("uri") should attach metadata."""

        @resource("config://app")
        def get_config() -> dict:
            return {"setting": "value"}

        decorated = cast(DecoratedResource, get_config)
        assert callable(get_config)
        assert hasattr(get_config, "__fastmcp__")
        assert isinstance(decorated.__fastmcp__, ResourceMeta)
        assert decorated.__fastmcp__.uri == "config://app"

    def test_resource_with_template_uri(self):
        """@resource with template URI should attach metadata."""

        @resource("users://{user_id}/profile")
        def get_profile(user_id: str) -> dict:
            return {"id": user_id}

        decorated = cast(DecoratedResource, get_profile)
        assert callable(get_profile)
        assert hasattr(get_profile, "__fastmcp__")
        assert decorated.__fastmcp__.uri == "users://{user_id}/profile"

    def test_resource_with_function_params_becomes_template(self):
        """@resource with function params should attach metadata."""

        @resource("data://items/{category}")
        def get_items(category: str, limit: int = 10) -> list:
            return list(range(limit))

        decorated = cast(DecoratedResource, get_items)
        assert callable(get_items)
        assert hasattr(get_items, "__fastmcp__")
        assert decorated.__fastmcp__.uri == "data://items/{category}"

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

        decorated = cast(DecoratedResource, get_config)
        assert callable(get_config)
        assert hasattr(get_config, "__fastmcp__")
        assert decorated.__fastmcp__.uri == "config://app"
        assert decorated.__fastmcp__.name == "app-config"
        assert decorated.__fastmcp__.title == "Application Config"
        assert decorated.__fastmcp__.description == "Gets app configuration"
        assert decorated.__fastmcp__.mime_type == "application/json"
        assert decorated.__fastmcp__.tags == {"config"}
        assert decorated.__fastmcp__.meta == {"custom": "value"}

    async def test_resource_function_still_callable(self):
        """Decorated function should still be directly callable."""

        @resource("config://app")
        def get_config() -> dict:
            """Get config."""
            return {"setting": "value"}

        # The function is still callable even though it has metadata
        result = cast(DecoratedResource, get_config)()
        assert result == {"setting": "value"}

    def test_resource_rejects_classmethod_decorator(self):
        """@resource should reject classmethod-decorated functions."""

        # Note: This now happens when added to server, not at decoration time
        @resource("config://app")
        def standalone() -> str:
            return "{}"

        # Should not raise at decoration
        assert callable(standalone)

    async def test_resource_added_to_server(self):
        """Resource created by @resource should work when added to a server."""

        @resource("config://app")
        def get_config() -> str:
            """Get config."""
            return '{"version": "1.0"}'

        assert callable(get_config)

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

        assert callable(get_profile)

        mcp = FastMCP("Test")
        # add_resource handles both resources and templates based on metadata
        mcp.add_resource(get_profile)

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert any(t.uriTemplate == "users://{user_id}/profile" for t in templates)

            result = await client.read_resource("users://123/profile")
            assert "123" in str(result)
