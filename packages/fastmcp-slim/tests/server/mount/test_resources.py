"""Tests for resource and template mounting."""

import json

from fastmcp import FastMCP


class TestResourcesAndTemplates:
    """Test mounting with resources and resource templates."""

    async def test_mount_with_resources(self):
        """Test mounting a server with resources."""
        main_app = FastMCP("MainApp")
        data_app = FastMCP("DataApp")

        @data_app.resource(uri="data://users")
        async def get_users() -> str:
            return "user1, user2"

        # Mount the data app
        main_app.mount(data_app, "data")

        # Resource should be accessible through main app
        resources = await main_app.list_resources()
        assert any(str(r.uri) == "data://data/users" for r in resources)

        # Check that resource can be accessed
        result = await main_app.read_resource("data://data/users")
        assert len(result.contents) == 1
        # Note: The function returns "user1, user2" which is not valid JSON
        # This test should be updated to return proper JSON or check the string directly
        assert result.contents[0].content == "user1, user2"

    async def test_mount_with_resource_templates(self):
        """Test mounting a server with resource templates."""
        main_app = FastMCP("MainApp")
        user_app = FastMCP("UserApp")

        @user_app.resource(uri="users://{user_id}/profile")
        def get_user_profile(user_id: str) -> str:
            return json.dumps({"id": user_id, "name": f"User {user_id}"})

        # Mount the user app
        main_app.mount(user_app, "api")

        # Template should be accessible through main app
        templates = await main_app.list_resource_templates()
        assert any(t.uri_template == "users://api/{user_id}/profile" for t in templates)

        # Check template instantiation
        result = await main_app.read_resource("users://api/123/profile")
        assert len(result.contents) == 1
        profile = json.loads(result.contents[0].content)
        assert profile["id"] == "123"
        assert profile["name"] == "User 123"

    async def test_mount_with_wildcard_resource_template(self):
        """Wildcard `{name*}` params must survive round-trip through a namespaced mount."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.resource("resource://multi/{extra*}")
        def multi(extra: str) -> str:
            return extra

        main_app.mount(sub_app, namespace="sub")

        result = await main_app.read_resource("resource://sub/multi/abc/def")
        assert result.contents[0].content == "abc/def"

        result = await main_app.read_resource("resource://sub/multi/abc")
        assert result.contents[0].content == "abc"

    async def test_adding_resource_after_mounting(self):
        """Test adding a resource after mounting."""
        main_app = FastMCP("MainApp")
        data_app = FastMCP("DataApp")

        # Mount the data app before adding resources
        main_app.mount(data_app, "data")

        # Add a resource after mounting
        @data_app.resource(uri="data://config")
        def get_config() -> str:
            return json.dumps({"version": "1.0"})

        # Resource should be accessible through main app
        resources = await main_app.list_resources()
        assert any(str(r.uri) == "data://data/config" for r in resources)

        # Check access to the resource
        result = await main_app.read_resource("data://data/config")
        assert len(result.contents) == 1
        config = json.loads(result.contents[0].content)
        assert config["version"] == "1.0"


class TestResourceUriPrefixing:
    """Test that resource and resource template URIs get prefixed when mounted (names are NOT prefixed)."""

    async def test_resource_uri_prefixing(self):
        """Test that resource URIs are prefixed when mounted (names are NOT prefixed)."""

        # Create a sub-app with a resource
        sub_app = FastMCP("SubApp")

        @sub_app.resource("resource://my_resource")
        def my_resource() -> str:
            return "Resource content"

        # Create main app and mount sub-app with prefix
        main_app = FastMCP("MainApp")
        main_app.mount(sub_app, "prefix")

        # Get resources from main app
        resources = await main_app.list_resources()

        # Should have prefixed key (using path format: resource://prefix/resource_name)
        assert any(str(r.uri) == "resource://prefix/my_resource" for r in resources)

        # The resource name should NOT be prefixed (only URI is prefixed)
        resource = next(
            r for r in resources if str(r.uri) == "resource://prefix/my_resource"
        )
        assert resource.name == "my_resource"

    async def test_resource_template_uri_prefixing(self):
        """Test that resource template URIs are prefixed when mounted (names are NOT prefixed)."""

        # Create a sub-app with a resource template
        sub_app = FastMCP("SubApp")

        @sub_app.resource("resource://user/{user_id}")
        def user_template(user_id: str) -> str:
            return f"User {user_id} data"

        # Create main app and mount sub-app with prefix
        main_app = FastMCP("MainApp")
        main_app.mount(sub_app, "prefix")

        # Get resource templates from main app
        templates = await main_app.list_resource_templates()

        # Should have prefixed key (using path format: resource://prefix/template_uri)
        assert any(
            t.uri_template == "resource://prefix/user/{user_id}" for t in templates
        )

        # The template name should NOT be prefixed (only URI template is prefixed)
        template = next(
            t for t in templates if t.uri_template == "resource://prefix/user/{user_id}"
        )
        assert template.name == "user_template"


class TestMountedResourceTemplateQueryParams:
    """Test that resource templates with query params work on mounted servers."""

    async def test_mounted_template_with_query_param(self):
        """Query params in resource templates should work through mount."""
        sub = FastMCP("Sub")

        @sub.resource("resource://greet{?name}")
        def greet(name: str = "World") -> str:
            return f"Hello, {name}!"

        main = FastMCP("Main")
        main.mount(sub, "sub")

        result = await main.read_resource("resource://sub/greet?name=Alice")
        assert result.contents[0].content == "Hello, Alice!"

    async def test_mounted_template_with_query_param_default(self):
        """Missing query params should use defaults through mount."""
        sub = FastMCP("Sub")

        @sub.resource("resource://greet{?name}")
        def greet(name: str = "World") -> str:
            return f"Hello, {name}!"

        main = FastMCP("Main")
        main.mount(sub, "sub")

        result = await main.read_resource("resource://sub/greet")
        assert result.contents[0].content == "Hello, World!"

    async def test_mounted_template_with_multiple_query_params(self):
        """Multiple query params should all pass through mount correctly."""
        sub = FastMCP("Sub")

        @sub.resource("resource://data/{id}{?format,verbose}")
        def get_data(id: str, format: str = "json", verbose: bool = False) -> str:
            return f"id={id} format={format} verbose={verbose}"

        main = FastMCP("Main")
        main.mount(sub, "api")

        result = await main.read_resource(
            "resource://api/data/42?format=xml&verbose=true"
        )
        assert result.contents[0].content == "id=42 format=xml verbose=True"

    async def test_mounted_template_with_partial_query_params(self):
        """Providing only some query params should use defaults for the rest."""
        sub = FastMCP("Sub")

        @sub.resource("resource://data/{id}{?format,limit}")
        def get_data(id: str, format: str = "json", limit: int = 10) -> str:
            return f"id={id} format={format} limit={limit}"

        main = FastMCP("Main")
        main.mount(sub, "api")

        result = await main.read_resource("resource://api/data/42?limit=5")
        assert result.contents[0].content == "id=42 format=json limit=5"
