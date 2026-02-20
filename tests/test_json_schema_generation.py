"""Tests for JSON schema generation from FastMCP BaseModel classes.

Validates that callable fields are properly excluded from generated schemas
using SkipJsonSchema annotations.
"""

from fastmcp.prompts.function_prompt import FunctionPrompt
from fastmcp.resources.function_resource import FunctionResource
from fastmcp.resources.template import FunctionResourceTemplate
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool import Tool
from fastmcp.tools.tool_transform import TransformedTool


class TestToolJsonSchema:
    """Test JSON schema generation for Tool classes."""

    def test_tool_json_schema_generation(self):
        """Verify Tool.model_json_schema() works without errors."""
        # This should not raise an error
        schema = Tool.model_json_schema()

        # Verify schema is valid
        assert schema["type"] == "object"
        assert "properties" in schema

        # Verify callable fields are excluded from schema
        assert "serializer" not in schema["properties"]
        # auth already uses exclude=True, so it shouldn't be in schema
        assert "auth" not in schema["properties"]

    def test_function_tool_json_schema_generation(self):
        """Verify FunctionTool.model_json_schema() works without errors."""

        def sample_tool(x: int, y: int) -> int:
            """Add two numbers."""
            return x + y

        tool = FunctionTool.from_function(sample_tool)

        # This should not raise an error
        schema = tool.model_json_schema()

        # Verify schema is valid
        assert schema["type"] == "object"
        assert "properties" in schema

        # Verify callable field 'fn' is excluded from schema
        assert "fn" not in schema["properties"]

    def test_transformed_tool_json_schema_generation(self):
        """Verify TransformedTool.model_json_schema() works without errors."""

        def parent_fn(x: int) -> int:
            return x * 2

        parent_tool = FunctionTool.from_function(parent_fn)
        transformed_tool = TransformedTool.from_tool(parent_tool, name="doubled")

        # This should not raise an error
        schema = transformed_tool.model_json_schema()

        # Verify schema is valid
        assert schema["type"] == "object"
        assert "properties" in schema

        # Verify callable fields are excluded from schema
        assert "fn" not in schema["properties"]
        assert "forwarding_fn" not in schema["properties"]
        assert "parent_tool" not in schema["properties"]


class TestResourceJsonSchema:
    """Test JSON schema generation for Resource classes."""

    def test_function_resource_json_schema_generation(self):
        """Verify FunctionResource.model_json_schema() works without errors."""

        def sample_resource() -> str:
            """Return sample data."""
            return "Hello, world!"

        resource = FunctionResource.from_function(
            sample_resource, uri="test://resource"
        )

        # This should not raise an error
        schema = resource.model_json_schema()

        # Verify schema is valid
        assert schema["type"] == "object"
        assert "properties" in schema

        # Verify callable field 'fn' is excluded from schema
        assert "fn" not in schema["properties"]
        # auth already uses exclude=True
        assert "auth" not in schema["properties"]

    def test_function_resource_template_json_schema_generation(self):
        """Verify FunctionResourceTemplate.model_json_schema() works without errors."""

        def sample_template(name: str) -> str:
            """Return greeting for name."""
            return f"Hello, {name}!"

        template = FunctionResourceTemplate.from_function(
            sample_template, uri_template="greeting://{name}"
        )

        # This should not raise an error
        schema = template.model_json_schema()

        # Verify schema is valid
        assert schema["type"] == "object"
        assert "properties" in schema

        # Verify callable field 'fn' is excluded from schema
        assert "fn" not in schema["properties"]


class TestPromptJsonSchema:
    """Test JSON schema generation for Prompt classes."""

    def test_function_prompt_json_schema_generation(self):
        """Verify FunctionPrompt.model_json_schema() works without errors."""

        def sample_prompt(topic: str) -> str:
            """Generate prompt about topic."""
            return f"Tell me about {topic}"

        prompt = FunctionPrompt.from_function(sample_prompt)

        # This should not raise an error
        schema = prompt.model_json_schema()

        # Verify schema is valid
        assert schema["type"] == "object"
        assert "properties" in schema

        # Verify callable field 'fn' is excluded from schema
        assert "fn" not in schema["properties"]
        # auth already uses exclude=True
        assert "auth" not in schema["properties"]


class TestJsonSchemaIntegration:
    """Integration tests for JSON schema generation across all classes."""

    def test_all_classes_generate_valid_schemas(self):
        """Verify all affected classes can generate valid JSON schemas."""

        # Create instances of all affected classes
        def tool_fn(x: int) -> int:
            return x

        def resource_fn() -> str:
            return "data"

        def template_fn(id: str) -> str:
            return f"data-{id}"

        def prompt_fn(input: str) -> str:
            return f"Prompt: {input}"

        tool = FunctionTool.from_function(tool_fn)
        transformed_tool = TransformedTool.from_tool(tool)
        resource = FunctionResource.from_function(resource_fn, uri="test://resource")
        template = FunctionResourceTemplate.from_function(
            template_fn, uri_template="test://{id}"
        )
        prompt = FunctionPrompt.from_function(prompt_fn)

        # All of these should succeed without errors
        schemas = [
            Tool.model_json_schema(),
            tool.model_json_schema(),
            transformed_tool.model_json_schema(),
            resource.model_json_schema(),
            template.model_json_schema(),
            prompt.model_json_schema(),
        ]

        # Verify all schemas are valid
        for schema in schemas:
            assert isinstance(schema, dict)
            assert schema["type"] == "object"
            assert "properties" in schema

    def test_callable_fields_not_in_any_schema(self):
        """Verify no callable fields appear in any generated schema."""

        # Define test functions
        def tool_fn(x: int) -> int:
            return x

        def resource_fn() -> str:
            return "data"

        def template_fn(id: str) -> str:
            return f"data-{id}"

        def prompt_fn(input: str) -> str:
            return f"Prompt: {input}"

        # Create instances
        tool = FunctionTool.from_function(tool_fn)
        transformed_tool = TransformedTool.from_tool(tool)
        resource = FunctionResource.from_function(resource_fn, uri="test://resource")
        template = FunctionResourceTemplate.from_function(
            template_fn, uri_template="test://{id}"
        )
        prompt = FunctionPrompt.from_function(prompt_fn)

        # List of (instance, callable_field_names) tuples
        test_cases = [
            (tool, ["fn", "serializer"]),
            (transformed_tool, ["fn", "forwarding_fn", "parent_tool", "serializer"]),
            (resource, ["fn"]),
            (template, ["fn"]),
            (prompt, ["fn"]),
        ]

        for instance, callable_fields in test_cases:
            schema = instance.model_json_schema()
            properties = schema.get("properties", {})

            # Verify none of the callable fields are in the schema
            for field in callable_fields:
                assert field not in properties, (
                    f"Callable field '{field}' found in schema for {type(instance).__name__}"
                )
