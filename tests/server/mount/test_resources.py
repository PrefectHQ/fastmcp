"""Tests for resource and template mounting."""

import json

import pytest
from mcp_types import TextResourceContents
from pydantic import AnyUrl

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy


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


@pytest.mark.parametrize("namespace", [None, "code", "localhost"])
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("reader", ["read_resource", "read_resource_mcp"])
@pytest.mark.parametrize("proxy", [False, True])
async def test_client_reads_advertised_file_resource(
    namespace: str | None,
    mode: str,
    reader: str,
    proxy: bool,
) -> None:
    child = FastMCP("Child")

    @child.resource("file:///docs/readme.txt")
    def readme() -> str:
        return "documentation"

    parent = FastMCP("Parent")
    parent.mount(create_proxy(child) if proxy else child, namespace=namespace)

    async with Client(parent, mode=mode) as client:
        resources = await client.list_resources()
        assert len(resources) == 1
        uri = resources[0].uri
        expected_uri = (
            f"file://{namespace}//docs/readme.txt"
            if namespace
            else "file:///docs/readme.txt"
        )
        assert str(uri) == expected_uri

        raw = await client.session.read_resource(str(uri))
        assert isinstance(raw.contents[0], TextResourceContents)
        assert raw.contents[0].text == "documentation"

        if reader == "read_resource":
            contents = await client.read_resource(uri)
        else:
            result = await client.read_resource_mcp(uri)
            contents = result.contents
        assert len(contents) == 1
        assert isinstance(contents[0], TextResourceContents)
        assert contents[0].text == "documentation"


@pytest.mark.parametrize("namespace", [None, "code", "localhost"])
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("reader", ["read_resource", "read_resource_mcp"])
@pytest.mark.parametrize("proxy", [False, True])
async def test_client_reads_advertised_file_template(
    namespace: str | None,
    mode: str,
    reader: str,
    proxy: bool,
) -> None:
    child = FastMCP("Child")

    @child.resource("file:///docs/{name}.txt")
    def document(name: str) -> str:
        return f"documentation:{name}"

    parent = FastMCP("Parent")
    parent.mount(create_proxy(child) if proxy else child, namespace=namespace)

    async with Client(parent, mode=mode) as client:
        templates = await client.list_resource_templates()
        assert len(templates) == 1
        uri = templates[0].uri_template.replace("{name}", "readme")
        expected_uri = (
            f"file://{namespace}//docs/readme.txt"
            if namespace
            else "file:///docs/readme.txt"
        )
        assert uri == expected_uri

        raw = await client.session.read_resource(uri)
        assert isinstance(raw.contents[0], TextResourceContents)
        assert raw.contents[0].text == "documentation:readme"

        if reader == "read_resource":
            contents = await client.read_resource(uri)
        else:
            result = await client.read_resource_mcp(uri)
            contents = result.contents
        assert len(contents) == 1
        assert isinstance(contents[0], TextResourceContents)
        assert contents[0].text == "documentation:readme"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("reader", ["read_resource", "read_resource_mcp"])
async def test_client_reads_http_resource_without_trailing_slash(
    mode: str,
    reader: str,
) -> None:
    server = FastMCP("HTTPResource")

    @server.resource("http://example.test")
    def document() -> str:
        return "documentation"

    async with Client(server, mode=mode) as client:
        if reader == "read_resource":
            contents = await client.read_resource("http://example.test")
        else:
            result = await client.read_resource_mcp("http://example.test")
            contents = result.contents

        assert len(contents) == 1
        assert isinstance(contents[0], TextResourceContents)
        assert contents[0].text == "documentation"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("template", [False, True])
async def test_namespaced_file_read_does_not_alias_parent_resource(
    mode: str, template: bool
) -> None:
    parent = FastMCP("Parent")

    @parent.resource("file://code/docs/readme.txt")
    def parent_document() -> str:
        return "parent"

    child = FastMCP("Child")
    if template:

        @child.resource("file:///docs/{name}.txt")
        def child_template(name: str) -> str:
            return f"child:{name}"

        expected = "child:readme"
    else:

        @child.resource("file:///docs/readme.txt")
        def child_document() -> str:
            return "child"

        expected = "child"

    parent.mount(child, namespace="code")

    async with Client(parent, mode=mode) as client:
        for uri, text in [
            ("file://code/docs/readme.txt", "parent"),
            ("file://code//docs/readme.txt", expected),
        ]:
            raw = await client.session.read_resource(uri)
            assert isinstance(raw.contents[0], TextResourceContents)
            assert raw.contents[0].text == text

            contents = await client.read_resource(uri)
            assert len(contents) == 1
            assert isinstance(contents[0], TextResourceContents)
            assert contents[0].text == text


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("proxy", [False, True])
async def test_client_reads_nested_namespaced_file_template(
    mode: str, proxy: bool
) -> None:
    child = FastMCP("Child")

    @child.resource("file:///docs/{name}.txt")
    def document(name: str) -> str:
        return f"documentation:{name}"

    middle = FastMCP("Middle")
    middle.mount(child, namespace="code")
    parent = FastMCP("Parent")
    parent.mount(create_proxy(middle) if proxy else middle, namespace="outer")

    async with Client(parent, mode=mode) as client:
        templates = await client.list_resource_templates()
        assert len(templates) == 1
        uri = templates[0].uri_template.replace("{name}", "readme")
        assert uri == "file://outer/code//docs/readme.txt"
        contents = await client.read_resource(uri)
        assert len(contents) == 1
        assert isinstance(contents[0], TextResourceContents)
        assert contents[0].text == "documentation:readme"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("proxy", [False, True])
async def test_client_preserves_resource_uri_normalization(
    mode: str, proxy: bool
) -> None:
    server = FastMCP("NormalizedResources")

    @server.resource("http://example.test")
    def document() -> str:
        return "documentation"

    @server.resource("http://example.test/docs/{name}")
    def http_template(name: str) -> str:
        return name

    @server.resource("file:///docs/read me.txt")
    def file_document() -> str:
        return "file documentation"

    async with Client(create_proxy(server) if proxy else server, mode=mode) as client:
        for uri, text in [
            ("http://example.test", "documentation"),
            ("http://EXAMPLE.TEST/docs/readme", "readme"),
            ("file:///docs/read me.txt", "file documentation"),
        ]:
            contents = await client.read_resource(uri)
            assert len(contents) == 1
            assert isinstance(contents[0], TextResourceContents)
            assert contents[0].text == text
            result = await client.read_resource_mcp(uri)
            assert isinstance(result.contents[0], TextResourceContents)
            assert result.contents[0].text == text


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("reader", ["read_resource", "read_resource_mcp"])
@pytest.mark.parametrize("template", [False, True])
@pytest.mark.parametrize("as_anyurl", [False, True])
@pytest.mark.parametrize("uri", ["file:///docs/readme.txt", "file:////docs/readme.txt"])
async def test_client_normalizes_file_uri_without_authority(
    mode: str, reader: str, template: bool, as_anyurl: bool, uri: str
) -> None:
    server = FastMCP("File URI aliases")
    if template:

        @server.resource("file:///docs/{name}.txt")
        def document_template(name: str) -> str:
            return f"documentation:{name}"

        expected = "documentation:readme"
    else:

        @server.resource("file:///docs/readme.txt")
        def document() -> str:
            return "documentation"

        expected = "documentation"

    async with Client(server, mode=mode) as client:
        raw = await client.session.read_resource("file:///docs/readme.txt")
        assert isinstance(raw.contents[0], TextResourceContents)
        assert raw.contents[0].text == expected

        requested = AnyUrl(uri) if as_anyurl else uri
        if reader == "read_resource":
            contents = await client.read_resource(requested)
        else:
            result = await client.read_resource_mcp(requested)
            contents = result.contents

        assert len(contents) == 1
        assert isinstance(contents[0], TextResourceContents)
        assert contents[0].text == expected
