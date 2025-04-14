from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from pydantic import AnyUrl, FileUrl

from fastmcp.exceptions import ResourceError
from fastmcp.resources import (
    FileResource,
    FunctionResource,
    ResourceManager,
    ResourceTemplate,
)
from fastmcp.settings import DuplicateBehavior


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
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=temp_file,
        )
        added = manager.add_resource(resource)
        assert added == resource
        assert manager.list_resources() == [resource]

    def test_add_duplicate_resource(self, temp_file: Path):
        """Test adding the same resource twice."""
        manager = ResourceManager()
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=temp_file,
        )
        first = manager.add_resource(resource)
        second = manager.add_resource(resource)
        assert first == second
        assert manager.list_resources() == [resource]

    def test_warn_on_duplicate_resources(self, temp_file: Path, caplog):
        """Test warning on duplicate resources."""
        manager = ResourceManager(duplicate_behavior=DuplicateBehavior.WARN)
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=temp_file,
        )
        manager.add_resource(resource)
        manager.add_resource(resource)
        assert "Resource already exists" in caplog.text

    def test_disable_warn_on_duplicate_resources(self, temp_file: Path, caplog):
        """Test disabling warning on duplicate resources."""
        manager = ResourceManager(duplicate_behavior=DuplicateBehavior.IGNORE)
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=temp_file,
        )
        manager.add_resource(resource)
        manager.add_resource(resource)
        assert "Resource already exists" not in caplog.text

    def test_error_on_duplicate_resources(self, temp_file: Path):
        """Test error on duplicate resources."""
        manager = ResourceManager(duplicate_behavior=DuplicateBehavior.ERROR)
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=temp_file,
        )
        manager.add_resource(resource)

        with pytest.raises(ValueError, match="Resource already exists"):
            manager.add_resource(resource)

    def test_replace_duplicate_resources(self, temp_file: Path):
        """Test replacing duplicate resources."""
        manager = ResourceManager(duplicate_behavior=DuplicateBehavior.REPLACE)

        resource1 = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test1",
            path=temp_file,
        )

        resource2 = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test2",  # Different name
            path=temp_file,
        )

        manager.add_resource(resource1)
        manager.add_resource(resource2)

        # Should have replaced the first resource with the second
        resources = manager.list_resources()
        assert len(resources) == 1
        assert resources[0].name == "test2"

    @pytest.mark.anyio
    async def test_get_resource(self, temp_file: Path):
        """Test getting a resource by URI."""
        manager = ResourceManager()
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=temp_file,
        )
        manager.add_resource(resource)
        retrieved = await manager.get_resource(resource.uri)
        assert retrieved == resource

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_get_unknown_resource(self):
        """Test getting a non-existent resource."""
        manager = ResourceManager()
        with pytest.raises(ResourceError, match="Unknown resource"):
            await manager.get_resource(AnyUrl("unknown://test"))

    def test_list_resources(self, temp_file: Path):
        """Test listing all resources."""
        manager = ResourceManager()
        resource1 = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test1",
            path=temp_file,
        )
        resource2 = FileResource(
            uri=FileUrl(f"file://{temp_file}2"),
            name="test2",
            path=temp_file,
        )
        manager.add_resource(resource1)
        manager.add_resource(resource2)
        resources = manager.list_resources()
        assert len(resources) == 2
        assert resources == [resource1, resource2]


class TestResourceTags:
    """Test functionality related to resource tags."""

    def test_add_resource_with_tags(self, temp_file: Path):
        """Test adding a resource with tags."""
        manager = ResourceManager()
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="weather_data",
            path=temp_file,
            tags={"weather", "data"},
        )
        manager.add_resource(resource)

        # Check that tags are preserved
        resources = manager.list_resources()
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
        resources = manager.list_resources()
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
        templates = manager.list_templates()
        assert len(templates) == 1
        assert templates[0].tags == {"users", "template", "data"}

    def test_filter_resources_by_tags(self, temp_file: Path):
        """Test filtering resources by tags."""
        manager = ResourceManager()

        # Create multiple resources with different tags
        resource1 = FileResource(
            uri=FileUrl(f"file://{temp_file}1"),
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
            r for r in manager.list_resources() if "internal" in r.tags
        ]
        assert len(internal_resources) == 2
        assert {r.name for r in internal_resources} == {"user_data", "system_data"}

        external_resources = [
            r for r in manager.list_resources() if "external" in r.tags
        ]
        assert len(external_resources) == 1
        assert external_resources[0].name == "weather_data"

    def test_import_resources_preserves_tags(self):
        """Test that importing resources preserves their tags."""
        source_manager = ResourceManager()

        async def get_data():
            return "Tagged data"

        resource = FunctionResource(
            uri=AnyUrl("data://tagged"),
            name="tagged_data",
            fn=get_data,
            tags={"test", "example", "data"},
        )

        source_manager.add_resource(resource)

        target_manager = ResourceManager()
        target_manager.import_resources(source_manager, "imported+")

        imported_resources = target_manager.list_resources()
        assert len(imported_resources) == 1
        assert imported_resources[0].tags == {"test", "example", "data"}

    def test_import_templates_preserves_tags(self):
        """Test that importing templates preserves their tags."""
        source_manager = ResourceManager()

        def user_template(user_id: str) -> str:
            return f"User {user_id}"

        template = ResourceTemplate.from_function(
            fn=user_template,
            uri_template="users://{user_id}",
            name="user_template",
            tags={"users", "template", "test"},
        )

        source_manager.add_template(template)

        target_manager = ResourceManager()
        target_manager.import_templates(source_manager, "imported+")

        imported_templates = target_manager.list_templates()
        assert len(imported_templates) == 1
        assert imported_templates[0].tags == {"users", "template", "test"}


class TestImports:
    def test_import_resources(self):
        """Test importing resources from one manager to another with a prefix."""
        # Setup source manager with resources
        source_manager = ResourceManager()

        # Create mock resource functions
        async def weather_fn():
            return "Weather data"

        async def traffic_fn():
            return "Traffic data"

        # Add resources to source manager
        weather_resource = FunctionResource(
            uri=AnyUrl("weather://forecast"),
            name="weather_forecast",
            description="Get weather forecast",
            mime_type="application/json",
            fn=weather_fn,
        )
        source_manager._resources["weather://forecast"] = weather_resource

        traffic_resource = FunctionResource(
            uri=AnyUrl("traffic://status"),
            name="traffic_status",
            description="Get traffic status",
            mime_type="application/json",
            fn=traffic_fn,
        )
        source_manager._resources["traffic://status"] = traffic_resource

        # Create target manager
        target_manager = ResourceManager()

        # Import resources from source to target
        prefix = "data+"
        target_manager.import_resources(source_manager, prefix)

        # Verify resources were imported with prefixes
        assert "data+weather://forecast" in target_manager._resources
        assert "data+traffic://status" in target_manager._resources

        # Verify the original resources still exist in source manager
        assert "weather://forecast" in source_manager._resources
        assert "traffic://status" in source_manager._resources

        # Verify the imported resources have the correct properties
        assert (
            target_manager._resources["data+weather://forecast"].name
            == "weather_forecast"
        )
        assert (
            target_manager._resources["data+weather://forecast"].description
            == "Get weather forecast"
        )
        assert (
            target_manager._resources["data+weather://forecast"].mime_type
            == "application/json"
        )

        assert (
            target_manager._resources["data+traffic://status"].name == "traffic_status"
        )
        assert (
            target_manager._resources["data+traffic://status"].description
            == "Get traffic status"
        )
        assert (
            target_manager._resources["data+traffic://status"].mime_type
            == "application/json"
        )

        # Since we're dealing with FunctionResource type, we can safely check function attributes
        assert isinstance(
            target_manager._resources["data+weather://forecast"], FunctionResource
        )
        assert isinstance(
            target_manager._resources["data+traffic://status"], FunctionResource
        )

        weather_resource = target_manager._resources["data+weather://forecast"]
        traffic_resource = target_manager._resources["data+traffic://status"]

        if hasattr(weather_resource, "fn") and hasattr(traffic_resource, "fn"):
            assert weather_resource.fn.__name__ == weather_fn.__name__
            assert traffic_resource.fn.__name__ == traffic_fn.__name__

    def test_import_templates(self):
        """Test importing resource templates from one manager to another with a prefix."""
        # Setup source manager with templates
        source_manager = ResourceManager()

        # Create mock template functions
        async def user_fn(**params):
            return f"User data for id {params.get('id')}"

        async def product_fn(**params):
            return f"Product data for id {params.get('id')}"

        # Add templates to source manager
        user_template = ResourceTemplate(
            uri_template="api://users/{id}",
            name="user_template",
            description="Get user by ID",
            mime_type="application/json",
            fn=user_fn,
            parameters={"id": {"type": "string", "description": "User ID"}},
        )
        source_manager._templates["api://users/{id}"] = user_template

        product_template = ResourceTemplate(
            uri_template="api://products/{id}",
            name="product_template",
            description="Get product by ID",
            mime_type="application/json",
            fn=product_fn,
            parameters={"id": {"type": "string", "description": "Product ID"}},
        )
        source_manager._templates["api://products/{id}"] = product_template

        # Create target manager
        target_manager = ResourceManager()

        # Import templates from source to target
        prefix = "shop+"
        target_manager.import_templates(source_manager, prefix)

        # Verify templates were imported with prefixes
        assert "shop+api://users/{id}" in target_manager._templates
        assert "shop+api://products/{id}" in target_manager._templates

        # Verify the original templates still exist in source manager
        assert "api://users/{id}" in source_manager._templates
        assert "api://products/{id}" in source_manager._templates

        # Verify the imported templates have the correct properties
        assert (
            target_manager._templates["shop+api://users/{id}"].name == "user_template"
        )
        assert (
            target_manager._templates["shop+api://users/{id}"].description
            == "Get user by ID"
        )
        assert (
            target_manager._templates["shop+api://users/{id}"].mime_type
            == "application/json"
        )
        assert target_manager._templates["shop+api://users/{id}"].parameters == {
            "id": {"type": "string", "description": "User ID"}
        }

        assert (
            target_manager._templates["shop+api://products/{id}"].name
            == "product_template"
        )
        assert (
            target_manager._templates["shop+api://products/{id}"].description
            == "Get product by ID"
        )
        assert (
            target_manager._templates["shop+api://products/{id}"].mime_type
            == "application/json"
        )
        assert target_manager._templates["shop+api://products/{id}"].parameters == {
            "id": {"type": "string", "description": "Product ID"}
        }

        # Verify the template functions were properly copied (only if the fn attribute exists)
        user_template = target_manager._templates["shop+api://users/{id}"]
        product_template = target_manager._templates["shop+api://products/{id}"]

        if hasattr(user_template, "fn") and hasattr(product_template, "fn"):
            assert user_template.fn.__name__ == user_fn.__name__
            assert product_template.fn.__name__ == product_fn.__name__

    def test_import_multiple_resource_types(self):
        """Test importing both resources and templates with the same prefix."""
        # Setup source manager with both resources and templates
        source_manager = ResourceManager()

        # Create mock functions
        async def resource_fn():
            return "Resource data"

        async def template_fn(**params):
            return f"Template data for id {params.get('id')}"

        # Add a resource to source manager
        resource = FunctionResource(
            uri=AnyUrl("data://resource"),
            name="test_resource",
            description="Test resource",
            mime_type="application/json",
            fn=resource_fn,
        )
        source_manager._resources["data://resource"] = resource

        # Add a template to source manager
        template = ResourceTemplate(
            uri_template="data://template/{id}",
            name="test_template",
            description="Test template",
            mime_type="application/json",
            fn=template_fn,
            parameters={"id": {"type": "string", "description": "ID parameter"}},
        )
        source_manager._templates["data://template/{id}"] = template

        # Create target manager
        target_manager = ResourceManager()

        # Import both resources and templates
        prefix = "test+"
        target_manager.import_resources(source_manager, prefix)
        target_manager.import_templates(source_manager, prefix)

        # Verify both resource types were imported with prefixes
        assert "test+data://resource" in target_manager._resources
        assert "test+data://template/{id}" in target_manager._templates
