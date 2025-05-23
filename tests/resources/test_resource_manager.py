from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from pydantic import AnyUrl, FileUrl

from fastmcp.exceptions import NotFoundError, ResourceError
from fastmcp.resources import (
    FileResource,
    FunctionResource,
    ResourceManager,
    ResourceTemplate,
)


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

    def test_add_resource(self, temp_file: Path):
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
        assert len(manager.get_resources()) == 1
        assert resource in manager.get_resources().values()

    def test_add_duplicate_resource(self, temp_file: Path):
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
        assert len(manager.get_resources()) == 1
        assert resource in manager.get_resources().values()

    def test_warn_on_duplicate_resources(self, temp_file: Path, caplog):
        """Test warning on duplicate resources."""
        manager = ResourceManager(duplicate_behavior="warn")

        file_url = "file://test-resource"
        resource = FileResource(
            uri=FileUrl(file_url),
            name="test_resource",
            path=temp_file,
        )

        manager.add_resource(resource)
        manager.add_resource(resource)

        assert "Resource already exists" in caplog.text
        # Should have the resource
        assert len(manager.get_resources()) == 1
        assert resource in manager.get_resources().values()

    def test_disable_warn_on_duplicate_resources(self, temp_file: Path, caplog):
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

    def test_error_on_duplicate_resources(self, temp_file: Path):
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

    def test_replace_duplicate_resources(self, temp_file: Path):
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
        resources = list(manager.get_resources().values())
        assert len(resources) == 1
        assert resources[0].name == "replacement"

    def test_ignore_duplicate_resources(self, temp_file: Path):
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
        resources = list(manager.get_resources().values())
        assert len(resources) == 1
        assert resources[0].name == "original"
        # Result should be the original resource
        assert result.name == "original"

    def test_warn_on_duplicate_templates(self, caplog):
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
        manager.add_template(template)

        assert "Template already exists" in caplog.text
        # Should have the template
        assert manager.get_templates() == {"test://{id}": template}

    def test_error_on_duplicate_templates(self):
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

    def test_replace_duplicate_templates(self):
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
        templates = list(manager.get_templates().values())
        assert len(templates) == 1
        assert templates[0].name == "replacement"

    def test_ignore_duplicate_templates(self):
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
        templates = list(manager.get_templates().values())
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

    def test_get_resources(self, temp_file: Path):
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
        resources = manager.get_resources()
        assert len(resources) == 2
        values = list(resources.values())
        assert resource1 in values
        assert resource2 in values


class TestResourceTags:
    """Test functionality related to resource tags."""

    def test_add_resource_with_tags(self, temp_file: Path):
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
        resources = list(manager.get_resources().values())
        assert len(resources) == 1
        assert resources[0].tags == {"weather", "data"}

    def test_add_function_resource_with_tags(self):
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
        resources = list(manager.get_resources().values())
        assert len(resources) == 1
        assert resources[0].tags == {"sample", "test", "data"}

    def test_add_template_with_tags(self):
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
        templates = list(manager.get_templates().values())
        assert len(templates) == 1
        assert templates[0].tags == {"users", "template", "data"}

    def test_filter_resources_by_tags(self, temp_file: Path):
        """Test filtering resources by tags."""
        manager = ResourceManager()

        # Create multiple resources with different tags
        resource1 = FileResource(
            uri=FileUrl("file://weather-data"),
            name="weather_data",
            path=temp_file,
            tags={"weather", "external"},
        )

        async def get_user_data():
            return "User data"

        resource2 = FunctionResource(
            uri=AnyUrl("data://users"),
            name="user_data",
            fn=get_user_data,
            tags={"users", "internal"},
        )

        async def get_system_data():
            return "System data"

        resource3 = FunctionResource(
            uri=AnyUrl("data://system"),
            name="system_data",
            fn=get_system_data,
            tags={"system", "internal"},
        )

        manager.add_resource(resource1)
        manager.add_resource(resource2)
        manager.add_resource(resource3)

        # Filter resources by tags
        internal_resources = [
            r for r in manager.get_resources().values() if "internal" in r.tags
        ]
        assert len(internal_resources) == 2
        assert {r.name for r in internal_resources} == {"user_data", "system_data"}

        external_resources = [
            r for r in manager.get_resources().values() if "external" in r.tags
        ]
        assert len(external_resources) == 1
        assert external_resources[0].name == "weather_data"


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

        manager.add_resource(resource, key=custom_key)

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

        manager.add_template(template, key=custom_key)

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

        manager.add_resource(resource, key=custom_key)

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

        manager.add_template(template, key=custom_key)

        # Using a URI that matches the custom key pattern
        resource = await manager.get_resource("custom://greet/world")
        assert isinstance(resource, FunctionResource)
        content = await resource.read()
        assert content == "Hello, world!"

        # Shouldn't work with the original template pattern
        with pytest.raises(NotFoundError, match="Unknown resource"):
            await manager.get_resource("greet://world")


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
