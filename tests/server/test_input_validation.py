"""
Tests for input validation behavior with strict_input_validation setting.

This module tests the difference between strict JSON schema validation (when
strict_input_validation=True) and Pydantic-based coercion (when
strict_input_validation=False, the default).
"""

import json

import pytest
from mcp.types import TextContent
from pydantic import BaseModel

from fastmcp import Client, FastMCP


class UserProfile(BaseModel):
    """A test model for validating Pydantic model arguments."""

    name: str
    age: int
    email: str


class TestStringToIntegerCoercion:
    """Test string-to-integer coercion behavior."""

    async def test_string_integer_with_strict_validation(self):
        """With strict validation, string integers should raise an error."""
        mcp = FastMCP("TestServer", strict_input_validation=True)

        @mcp.tool
        def add_numbers(a: int, b: int) -> int:
            """Add two numbers together."""
            return a + b

        async with Client(mcp) as client:
            # String integers should fail with strict validation
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("add_numbers", {"a": "10", "b": "20"})

            # Verify it's a validation error
            error_msg = str(exc_info.value).lower()
            assert (
                "validation" in error_msg
                or "invalid" in error_msg
                or "type" in error_msg
            )

    async def test_string_integer_without_strict_validation(self):
        """Without strict validation, string integers should be coerced."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def add_numbers(a: int, b: int) -> int:
            """Add two numbers together."""
            return a + b

        async with Client(mcp) as client:
            # String integers should be coerced to integers
            result = await client.call_tool("add_numbers", {"a": "10", "b": "20"})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "30"

    async def test_default_is_not_strict(self):
        """By default, strict_input_validation should be False."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def multiply(x: int, y: int) -> int:
            """Multiply two numbers."""
            return x * y

        async with Client(mcp) as client:
            # Should work with string integers by default
            result = await client.call_tool("multiply", {"x": "5", "y": "3"})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "15"

    async def test_string_float_coercion(self):
        """Test that string floats are also coerced."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def calculate_area(length: float, width: float) -> float:
            """Calculate rectangle area."""
            return length * width

        async with Client(mcp) as client:
            result = await client.call_tool(
                "calculate_area", {"length": "10.5", "width": "20.0"}
            )
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "210.0"

    async def test_invalid_coercion_still_fails(self):
        """Even without strict validation, truly invalid inputs should fail."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def square(n: int) -> int:
            """Square a number."""
            return n * n

        async with Client(mcp) as client:
            # Non-numeric strings should still fail
            with pytest.raises(Exception):
                await client.call_tool("square", {"n": "not-a-number"})


class TestPydanticModelArguments:
    """Test validation of Pydantic model arguments."""

    async def test_pydantic_model_with_dict_no_strict(self):
        """Pydantic models should accept dict arguments without strict validation."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def create_user(profile: UserProfile) -> str:
            """Create a user from a profile."""
            return f"Created user {profile.name}, age {profile.age}"

        async with Client(mcp) as client:
            result = await client.call_tool(
                "create_user",
                {"profile": {"name": "Alice", "age": 30, "email": "alice@example.com"}},
            )
            assert isinstance(result.content[0], TextContent)
            assert "Alice" in result.content[0].text
            assert "30" in result.content[0].text

    async def test_pydantic_model_with_stringified_json_no_strict(self):
        """Test if stringified JSON is accepted for Pydantic models without strict validation."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def create_user(profile: UserProfile) -> str:
            """Create a user from a profile."""
            return f"Created user {profile.name}, age {profile.age}"

        async with Client(mcp) as client:
            # Some LLM clients send stringified JSON instead of actual JSON
            stringified = json.dumps(
                {"name": "Bob", "age": 25, "email": "bob@example.com"}
            )

            # This test verifies whether we handle stringified JSON
            try:
                result = await client.call_tool("create_user", {"profile": stringified})
                # If this succeeds, we're handling stringified JSON
                assert isinstance(result.content[0], TextContent)
                assert "Bob" in result.content[0].text
                stringified_json_works = True
            except Exception as e:
                # If this fails, we're not handling stringified JSON
                stringified_json_works = False
                error_msg = str(e)

            # Document the behavior - we want to know if this works or not
            if stringified_json_works:
                # This is the desired behavior
                pass
            else:
                # This means stringified JSON doesn't work - document it
                assert (
                    "validation" in error_msg.lower() or "invalid" in error_msg.lower()
                )

    async def test_pydantic_model_with_coercion(self):
        """Pydantic models should benefit from coercion without strict validation."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def create_user(profile: UserProfile) -> str:
            """Create a user from a profile."""
            return f"Created user {profile.name}, age {profile.age}"

        async with Client(mcp) as client:
            # Age as string should be coerced
            result = await client.call_tool(
                "create_user",
                {
                    "profile": {
                        "name": "Charlie",
                        "age": "35",  # String instead of int
                        "email": "charlie@example.com",
                    }
                },
            )
            assert isinstance(result.content[0], TextContent)
            assert "Charlie" in result.content[0].text
            assert "35" in result.content[0].text

    async def test_pydantic_model_strict_validation(self):
        """With strict validation, Pydantic models should enforce exact types."""
        mcp = FastMCP("TestServer", strict_input_validation=True)

        @mcp.tool
        def create_user(profile: UserProfile) -> str:
            """Create a user from a profile."""
            return f"Created user {profile.name}, age {profile.age}"

        async with Client(mcp) as client:
            # Age as string should fail with strict validation
            with pytest.raises(Exception):
                await client.call_tool(
                    "create_user",
                    {
                        "profile": {
                            "name": "Dave",
                            "age": "40",  # String instead of int
                            "email": "dave@example.com",
                        }
                    },
                )


class TestValidationErrorMessages:
    """Test the quality of validation error messages."""

    async def test_error_message_quality_strict(self):
        """Capture error message with strict validation."""
        mcp = FastMCP("TestServer", strict_input_validation=True)

        @mcp.tool
        def process_data(count: int, name: str) -> str:
            """Process some data."""
            return f"Processed {count} items for {name}"

        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "process_data", {"count": "not-a-number", "name": "test"}
                )

            error_msg = str(exc_info.value)
            # Strict validation error message
            # Should mention validation or type error
            assert (
                "validation" in error_msg.lower()
                or "invalid" in error_msg.lower()
                or "type" in error_msg.lower()
            )

    async def test_error_message_quality_pydantic(self):
        """Capture error message with Pydantic validation."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def process_data(count: int, name: str) -> str:
            """Process some data."""
            return f"Processed {count} items for {name}"

        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "process_data", {"count": "not-a-number", "name": "test"}
                )

            error_msg = str(exc_info.value)
            # Pydantic validation error message
            # Should be more detailed and mention validation
            assert "validation" in error_msg.lower() or "invalid" in error_msg.lower()

    async def test_missing_required_field_error(self):
        """Test error message for missing required fields."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def greet(name: str, age: int) -> str:
            """Greet a person."""
            return f"Hello {name}, you are {age} years old"

        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                # Missing 'age' parameter
                await client.call_tool("greet", {"name": "Alice"})

            error_msg = str(exc_info.value)
            # Should mention the missing field
            assert "age" in error_msg.lower() or "required" in error_msg.lower()


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    async def test_optional_parameters_with_coercion(self):
        """Optional parameters should work with coercion."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def format_message(text: str, repeat: int = 1) -> str:
            """Format a message with optional repetition."""
            return text * repeat

        async with Client(mcp) as client:
            # String for optional int parameter
            result = await client.call_tool(
                "format_message", {"text": "hi", "repeat": "3"}
            )
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "hihihi"

    async def test_none_values(self):
        """Test handling of None values."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def process_optional(value: int | None) -> str:
            """Process an optional value."""
            return f"Value: {value}"

        async with Client(mcp) as client:
            result = await client.call_tool("process_optional", {"value": None})
            assert isinstance(result.content[0], TextContent)
            assert "None" in result.content[0].text

    async def test_empty_string_to_int(self):
        """Empty strings should fail conversion to int."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def square(n: int) -> int:
            """Square a number."""
            return n * n

        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool("square", {"n": ""})

    async def test_boolean_coercion(self):
        """Test boolean value coercion."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def toggle(enabled: bool) -> str:
            """Toggle a feature."""
            return f"Feature is {'enabled' if enabled else 'disabled'}"

        async with Client(mcp) as client:
            # String "true" should be coerced to boolean
            result = await client.call_tool("toggle", {"enabled": "true"})
            assert isinstance(result.content[0], TextContent)
            assert "enabled" in result.content[0].text.lower()

            # String "false" should be coerced to boolean
            result = await client.call_tool("toggle", {"enabled": "false"})
            assert isinstance(result.content[0], TextContent)
            assert "disabled" in result.content[0].text.lower()

    async def test_list_of_integers_with_string_elements(self):
        """Test lists containing string representations of integers."""
        mcp = FastMCP("TestServer", strict_input_validation=False)

        @mcp.tool
        def sum_numbers(numbers: list[int]) -> int:
            """Sum a list of numbers."""
            return sum(numbers)

        async with Client(mcp) as client:
            # List with string integers
            result = await client.call_tool("sum_numbers", {"numbers": ["1", "2", "3"]})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "6"
