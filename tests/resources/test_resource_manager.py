from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from pydantic import AnyUrl, FileUrl

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
        manager = ResourceManager()
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
        manager = ResourceManager(warn_on_duplicate_resources=False)
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=temp_file,
        )
        manager.add_resource(resource)
        manager.add_resource(resource)
        assert "Resource already exists" not in caplog.text

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
        with pytest.raises(ValueError, match="Unknown resource"):
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
