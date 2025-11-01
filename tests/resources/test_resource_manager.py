from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from pydantic import AnyUrl, FileUrl

from fastmcp.exceptions import NotFoundError, ResourceError
from fastmcp.resources import (
    FileResource,
    ResourceManager,
    ResourceTemplate,
)
from fastmcp.resources.resource import FunctionResource
from fastmcp.utilities.tests import caplog_for_fastmcp


@pytest.fixture
def temp_file():
    """Create a temporary file for testing.

    File is automatically cleaned up after the test if it still exists.
    """
    content = "test content"
    with NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(content)
        path = Path(f.name).resolve()
    yield path
    try:
        path.unlink()
    except FileNotFoundError:
        pass  # File was already deleted by the test


class TestResourceManager:
    """Test ResourceManager functionality."""

    async def test_add_resource(self, temp_file: Path):
        """Test adding a resource."""
        manager = ResourceManager()
        file_url = "file://test-resource"
        resource = FileResource(
            uri=FileUrl(file_url),
            name="test",
            path=temp_file,
        )
        added = manager.add_resource(resource)
        assert added == resource
        # Get the actual key from the resource manager
        resources = await manager.get_resources()
        assert len(resources) == 1
        assert resource in resources.values()

    async def test_add_duplicate_resource(self, temp_file: Path):
        """Test adding the same resource twice."""
        manager = ResourceManager()
        file_url = "file://test-resource"
        resource = FileResource(
            uri=FileUrl(file_url),
            name="test",
            path=temp_file,
        )
        first = manager.add_resource(resource)
        second = manager.add_resource(resource)
        assert first == second
        # Check the resource is there
        resources = await manager.get_resources()
        assert len(resources) == 1
        assert resource in resources.values()

    async def test_warn_on_duplicate_resources(self, temp_file: Path, caplog):
        """Test warning on duplicate resources."""
        manager = ResourceManager(duplicate_behavior="warn")

        file_url = "file://test-resource"
        resource = FileResource(
            uri=FileUrl(file_url),
            name="test_resource",
            path=temp_file,
        )

        manager.add_resource(resource)

        with caplog_for_fastmcp(caplog):
            manager.add_resource(resource)

        assert "Resource already exists" in caplog.text
        # Should have the resource
        resources = await manager.get_resources()
        assert len(resources) == 1
        assert resource in resources.values()

    async def test_disable_warn_on_duplicate_resources(self, temp_file: Path, caplog):
        """Test disabling warning on duplicate resources."""
        manager = ResourceManager(duplicate_behavior="ignore")
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file.name}"),
            name="test",
            path=temp_file,
        )
        manager.add_resource(resource)
        manager.add_resource(resource)
        assert "Resource already exists" not in caplog.text

    async def test_error_on_duplicate_resources(self, temp_file: Path):
        """Test error on duplicate resources."""
        manager = ResourceManager(duplicate_behavior="error")

        resource = FileResource(
            uri=FileUrl(f"file://{temp_file.name}"),
            name="test_resource",
            path=temp_file,
        )

        manager.add_resource(resource)

        with pytest.raises(ValueError, match="Resource already exists"):
            manager.add_resource(resource)

    async def test_replace_duplicate_resources(self, temp_file: Path):
        """Test replacing duplicate resources."""
        manager = ResourceManager(duplicate_behavior="replace")

        file_url = "file://test-resource"
        resource1 = FileResource(
            uri=FileUrl(file_url),
            name="original",
            path=temp_file,
        )

        resource2 = FileResource(
            uri=FileUrl(file_url),
            name="replacement",
            path=temp_file,
        )

        manager.add_resource(resource1)
        manager.add_resource(resource2)

        # Should have replaced with the new resource
        resources = await manager.get_resources()
        resource_list = list(resources.values())
        assert len(resource_list) == 1
        assert resource_list[0].name == "replacement"

    async def test_ignore_duplicate_resources(self, temp_file: Path):
        """Test ignoring duplicate resources."""
        manager = ResourceManager(duplicate_behavior="ignore")

        file_url = "file://test-resource"
        resource1 = FileResource(
            uri=FileUrl(file_url),
            name="original",
            path=temp_file,
        )

        resource2 = FileResource(
            uri=FileUrl(file_url),
            name="replacement",
            path=temp_file,
        )

        manager.add_resource(resource1)
        result = manager.add_resource(resource2)

        # Should keep the original
        resources = await manager.get_resources()
        resource_list = list(resources.values())
        assert len(resource_list) == 1
        assert resource_list[0].name == "original"
        # Result should be the original resource
        assert result.name == "original"

    async def test_warn_on_duplicate_templates(self, caplog):
        """Test warning on duplicate templates."""
        manager = ResourceManager(duplicate_behavior="warn")

        def template_fn(id: str) -> str:
            return f"Template {id}"

        template = ResourceTemplate.from_function(
            fn=template_fn,
            uri_template="test://{id}",
            name="test_template",
        )

        manager.add_template(template)

        with caplog_for_fastmcp(caplog):
            manager.add_template(template)

        assert "Template already exists" in caplog.text
        # Should have the template
        templates = await manager.get_resource_templates()
        assert templates == {"test://{id}": template}

    async def test_error_on_duplicate_templates(self):
        """Test error on duplicate templates."""
        manager = ResourceManager(duplicate_behavior="error")

        def template_fn(id: str) -> str:
            return f"Template {id}"

        template = ResourceTemplate.from_function(
            fn=template_fn,
            uri_template="test://{id}",
            name="test_template",
        )

        manager.add_template(template)

        with pytest.raises(ValueError, match="Template already exists"):
            manager.add_template(template)

    async def test_replace_duplicate_templates(self):
        """Test replacing duplicate templates."""
        manager = ResourceManager(duplicate_behavior="replace")

        def original_fn(id: str) -> str:
            return f"Original {id}"

        def replacement_fn(id: str) -> str:
            return f"Replacement {id}"

        template1 = ResourceTemplate.from_function(
            fn=original_fn,
            uri_template="test://{id}",
            name="original",
        )

        template2 = ResourceTemplate.from_function(
            fn=replacement_fn,
            uri_template="test://{id}",
            name="replacement",
        )

        manager.add_template(template1)
        manager.add_template(template2)

        # Should have replaced with the new template
        templates_dict = await manager.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].name == "replacement"

    async def test_ignore_duplicate_templates(self):
        """Test ignoring duplicate templates."""
        manager = ResourceManager(duplicate_behavior="ignore")

        def original_fn(id: str) -> str:
            return f"Original {id}"

        def replacement_fn(id: str) -> str:
            return f"Replacement {id}"

        template1 = ResourceTemplate.from_function(
            fn=original_fn,
            uri_template="test://{id}",
            name="original",
        )

        template2 = ResourceTemplate.from_function(
            fn=replacement_fn,
            uri_template="test://{id}",
            name="replacement",
        )

        manager.add_template(template1)
        result = manager.add_template(template2)

        # Should keep the original
        templates_dict = await manager.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].name == "original"
        # Result should be the original template
        assert result.name == "original"

    async def test_get_resource(self, temp_file: Path):
        """Test getting a resource by URI."""
        manager = ResourceManager()
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file.name}"),
            name="test",
            path=temp_file,
        )
        manager.add_resource(resource)
        retrieved = await manager.get_resource(resource.uri)
        assert retrieved == resource

    async def test_get_resource_from_template(self):
        """Test getting a resource through a template."""
        manager = ResourceManager()

        def greet(name: str) -> str:
            return f"Hello, {name}!"

        template = ResourceTemplate.from_function(
            fn=greet,
            uri_template="greet://{name}",
            name="greeter",
        )
        manager._templates[template.uri_template] = template

        resource = await manager.get_resource(AnyUrl("greet://world"))
        assert isinstance(resource, FunctionResource)
        content = await resource.read()
        assert content == "Hello, world!"

    async def test_get_unknown_resource(self):
        """Test getting a non-existent resource."""
        manager = ResourceManager()
        with pytest.raises(NotFoundError, match="Unknown resource"):
            await manager.get_resource(AnyUrl("unknown://test"))

    async def test_get_resources(self, temp_file: Path):
        """Test retrieving all resources."""
        manager = ResourceManager()
        file_url1 = "file://test-resource1"
        resource1 = FileResource(
            uri=FileUrl(file_url1),
            name="test1",
            path=temp_file,
        )
        file_url2 = "file://test-resource2"
        resource2 = FileResource(
            uri=FileUrl(file_url2),
            name="test2",
            path=temp_file,
        )
        manager.add_resource(resource1)
        manager.add_resource(resource2)
        resources = await manager.get_resources()
        assert len(resources) == 2
        values = list(resources.values())
        assert resource1 in values
        assert resource2 in values


class TestResourceTags:
    """Test functionality related to resource tags."""

    async def test_add_resource_with_tags(self, temp_file: Path):
        """Test adding a resource with tags."""
        manager = ResourceManager()
        resource = FileResource(
            uri=FileUrl("file://weather-data"),
            name="weather_data",
            path=temp_file,
            tags={"weather", "data"},
        )
        manager.add_resource(resource)

        # Check that tags are preserved
        resources_dict = await manager.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].tags == {"weather", "data"}

    async def test_add_function_resource_with_tags(self):
        """Test adding a function resource with tags."""
        manager = ResourceManager()

        async def get_data():
            return "Sample data"

        resource = FunctionResource(
            uri=AnyUrl("data://sample"),
            name="sample_data",
            description="Sample data resource",
            mime_type="text/plain",
            fn=get_data,
            tags={"sample", "test", "data"},
        )

        manager.add_resource(resource)
        resources_dict = await manager.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].tags == {"sample", "test", "data"}

    async def test_add_template_with_tags(self):
        """Test adding a resource template with tags."""
        manager = ResourceManager()

        def user_data(user_id: str) -> str:
            return f"Data for user {user_id}"

        template = ResourceTemplate.from_function(
            fn=user_data,
            uri_template="users://{user_id}",
            name="user_template",
            description="Get user data by ID",
            tags={"users", "template", "data"},
        )

        manager.add_template(template)
        templates_dict = await manager.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].tags == {"users", "template", "data"}

    async def test_filter_resources_by_tags(self, temp_file: Path):
        """Test filtering resources by tags."""
        manager = ResourceManager()

        # Create multiple resources with different tags
        resource1 = FileResource(
            uri=FileUrl("file://weather-data"),
            name="weather_data",
            path=temp_file,
            tags={"weather", "data"},
        )

        async def get_user_data():
            return "User data"

        resource2 = FunctionResource(
            uri=AnyUrl("data://users"),
            name="user_data",
            description="User data resource",
            mime_type="text/plain",
            fn=get_user_data,
            tags={"users", "data"},
        )

        async def get_system_data():
            return "System data"

        resource3 = FunctionResource(
            uri=AnyUrl("data://system"),
            name="system_data",
            description="System data resource",
            mime_type="text/plain",
            fn=get_system_data,
            tags={"system", "admin"},
        )

        manager.add_resource(resource1)
        manager.add_resource(resource2)
        manager.add_resource(resource3)

        # Filter by tags
        resources_dict = await manager.get_resources()
        data_resources = [r for r in resources_dict.values() if "data" in r.tags]
        assert len(data_resources) == 2
        assert {r.name for r in data_resources} == {"weather_data", "user_data"}

        admin_resources = [r for r in resources_dict.values() if "admin" in r.tags]
        assert len(admin_resources) == 1
        assert admin_resources[0].name == "system_data"


class TestCustomResourceKeys:
    """Test adding resources and templates with custom keys."""

    def test_add_resource_with_custom_key(self, temp_file: Path):
        """Test adding a resource with a custom key different from its URI."""
        manager = ResourceManager()
        original_uri = "data://test/resource"
        custom_key = "custom://resource/key"

        # Create a function resource instead of file resource to avoid path issues
        async def get_data():
            return "Test data"

        resource = FunctionResource(
            uri=AnyUrl(original_uri),
            name="test_resource",
            fn=get_data,
        )

        # Use model_copy to create a new resource with the custom key
        resource_with_custom_key = resource.model_copy(key=custom_key)
        manager.add_resource(resource_with_custom_key)

        # Resource should be accessible via custom key
        assert custom_key in manager._resources
        # But not via its original URI
        assert original_uri not in manager._resources
        # The resource's internal URI remains unchanged
        assert str(manager._resources[custom_key].uri) == original_uri

    def test_add_template_with_custom_key(self):
        """Test adding a template with a custom key different from its URI template."""
        manager = ResourceManager()

        def template_fn(id: str) -> str:
            return f"Template {id}"

        original_uri_template = "test://{id}"
        custom_key = "custom://{id}/template"

        template = ResourceTemplate.from_function(
            fn=template_fn,
            uri_template=original_uri_template,
            name="test_template",
        )

        # Use model_copy to create a new template with the custom key
        template_with_custom_key = template.model_copy(key=custom_key)
        manager.add_template(template_with_custom_key)

        # Template should be accessible via custom key
        assert custom_key in manager._templates
        # But not via its original URI template
        assert original_uri_template not in manager._templates
        # The template's internal URI template remains unchanged
        assert str(manager._templates[custom_key].uri_template) == original_uri_template

    async def test_get_resource_with_custom_key(self, temp_file: Path):
        """Test that get_resource works with resources added with custom keys."""
        manager = ResourceManager()
        original_uri = "data://test/resource"
        custom_key = "custom://resource/path"

        # Create a function resource instead of file resource to avoid path issues
        async def get_data():
            return "Test data"

        resource = FunctionResource(
            uri=AnyUrl(original_uri),
            name="test_resource",
            fn=get_data,
        )

        # Use model_copy to create a new resource with the custom key
        resource_with_custom_key = resource.model_copy(key=custom_key)
        manager.add_resource(resource_with_custom_key)

        # Should be retrievable by the custom key
        retrieved = await manager.get_resource(custom_key)
        assert retrieved is not None
        assert str(retrieved.uri) == original_uri

        # Should NOT be retrievable by the original URI
        with pytest.raises(NotFoundError, match="Unknown resource"):
            await manager.get_resource(original_uri)

    async def test_get_resource_from_template_with_custom_key(self):
        """Test that templates with custom keys can create resources."""
        manager = ResourceManager()

        def greet(name: str) -> str:
            return f"Hello, {name}!"

        original_template = "greet://{name}"
        custom_key = "custom://greet/{name}"

        template = ResourceTemplate.from_function(
            fn=greet,
            uri_template=original_template,
            name="custom_greeter",
        )

        # Use model_copy to create a new template with the custom key
        template_with_custom_key = template.model_copy(key=custom_key)
        manager.add_template(template_with_custom_key)

        # Using a URI that matches the custom key pattern
        resource = await manager.get_resource("custom://greet/world")
        assert isinstance(resource, FunctionResource)
        content = await resource.read()
        assert content == "Hello, world!"

        # Shouldn't work with the original template pattern
        with pytest.raises(NotFoundError, match="Unknown resource"):
            await manager.get_resource("greet://world")


class TestQueryOnlyTemplates:
    """Test resource templates with only query parameters (no path params)."""

    async def test_template_with_only_query_params_no_query_string(self):
        """Test that templates with only query params work without query string.

        Regression test for bug where empty parameter dict {} was treated as falsy,
        causing templates with only query parameters to fail when no query string
        was provided in the URI.
        """
        manager = ResourceManager()

        def get_config(format: str = "json") -> str:
            return f"Config in {format} format"

        template = ResourceTemplate.from_function(
            fn=get_config,
            uri_template="data://config{?format}",
            name="config",
        )
        manager.add_template(template)

        # Should work without query param (uses default)
        resource = await manager.get_resource("data://config")
        content = await resource.read()
        assert content == "Config in json format"

        # Should also work via read_resource
        content = await manager.read_resource("data://config")
        assert content == "Config in json format"

    async def test_template_with_only_query_params_with_query_string(self):
        """Test that templates with only query params work with query string."""
        manager = ResourceManager()

        def get_config(format: str = "json") -> str:
            return f"Config in {format} format"

        template = ResourceTemplate.from_function(
            fn=get_config,
            uri_template="data://config{?format}",
            name="config",
        )
        manager.add_template(template)

        # Should work with query param (overrides default)
        resource = await manager.get_resource("data://config?format=xml")
        content = await resource.read()
        assert content == "Config in xml format"

        # Should also work via read_resource
        content = await manager.read_resource("data://config?format=xml")
        assert content == "Config in xml format"

    async def test_template_with_only_multiple_query_params(self):
        """Test template with only multiple query parameters."""
        manager = ResourceManager()

        def get_data(format: str = "json", limit: int = 10) -> str:
            return f"Data in {format} (limit: {limit})"

        template = ResourceTemplate.from_function(
            fn=get_data,
            uri_template="data://items{?format,limit}",
            name="items",
        )
        manager.add_template(template)

        # No query params - use all defaults
        content = await manager.read_resource("data://items")
        assert content == "Data in json (limit: 10)"

        # Partial query params
        content = await manager.read_resource("data://items?format=xml")
        assert content == "Data in xml (limit: 10)"

        # All query params
        content = await manager.read_resource("data://items?format=xml&limit=20")
        assert content == "Data in xml (limit: 20)"

    async def test_has_resource_with_query_only_template(self):
        """Test that has_resource() works with query-only templates.

        Regression test for bug where empty parameter dict {} was treated as falsy,
        causing has_resource() to return False for query-only templates when no
        query string was provided.
        """
        manager = ResourceManager()

        def get_config(format: str = "json") -> str:
            return f"Config in {format} format"

        template = ResourceTemplate.from_function(
            fn=get_config,
            uri_template="data://config{?format}",
            name="config",
        )
        manager.add_template(template)

        # Should find resource without query param (uses default)
        assert await manager.has_resource("data://config")

        # Should also find resource with query param
        assert await manager.has_resource("data://config?format=xml")


class TestResourceErrorHandling:
    """Test error handling in the ResourceManager."""

    async def test_resource_error_passthrough(self):
        """Test that ResourceErrors are passed through directly."""
        manager = ResourceManager()

        async def error_resource():
            """Resource that raises a ResourceError."""
            raise ResourceError("Specific resource error")

        resource = FunctionResource(
            uri=AnyUrl("error://resource"),
            name="error_resource",
            fn=error_resource,
        )
        manager.add_resource(resource)

        with pytest.raises(ResourceError, match="Specific resource error"):
            await manager.read_resource("error://resource")

    async def test_template_resource_error_passthrough(self):
        """Test that ResourceErrors from template-generated resources are passed through."""
        manager = ResourceManager()

        def error_template(param: str):
            """Template that raises a ResourceError."""
            raise ResourceError(f"Template error with param {param}")

        template = ResourceTemplate.from_function(
            fn=error_template,
            uri_template="error://{param}",
            name="error_template",
        )
        manager.add_template(template)

        with pytest.raises(ResourceError) as excinfo:
            await manager.read_resource("error://test")

        # The original error message should be included in the ValueError
        assert "Template error with param test" in str(excinfo.value)

    async def test_exception_converted_to_resource_error_with_details(self):
        """Test that other exceptions are converted to ResourceError with details by default."""
        manager = ResourceManager()

        async def buggy_resource():
            """Resource that raises a ValueError."""
            raise ValueError("Internal error details")

        resource = FunctionResource(
            uri=AnyUrl("buggy://resource"),
            name="buggy_resource",
            fn=buggy_resource,
        )
        manager.add_resource(resource)

        with pytest.raises(ResourceError) as excinfo:
            await manager.read_resource("buggy://resource")

        # The error message should include the original exception details
        assert "Error reading resource 'buggy://resource'" in str(excinfo.value)
        assert "Internal error details" in str(excinfo.value)

    async def test_exception_converted_to_masked_resource_error(self):
        """Test that other exceptions are masked when enabled."""
        manager = ResourceManager(mask_error_details=True)

        async def buggy_resource():
            """Resource that raises a ValueError."""
            raise ValueError("Internal error details")

        resource = FunctionResource(
            uri=AnyUrl("buggy://resource"),
            name="buggy_resource",
            fn=buggy_resource,
        )
        manager.add_resource(resource)

        with pytest.raises(ResourceError) as excinfo:
            await manager.read_resource("buggy://resource")

        # The error message should not include the original exception details
        assert "Error reading resource 'buggy://resource'" in str(excinfo.value)
        assert "Internal error details" not in str(excinfo.value)
