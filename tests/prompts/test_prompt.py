import pytest
from mcp.types import EmbeddedResource, TextResourceContents
from pydantic import FileUrl

from fastmcp.prompts.prompt import (
    Message,
    Prompt,
    PromptMessage,
    TextContent,
)


class TestRenderPrompt:
    async def test_basic_fn(self):
        def fn() -> str:
            return "Hello, world!"

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, world!")
            )
        ]

    async def test_async_fn(self):
        async def fn() -> str:
            return "Hello, world!"

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, world!")
            )
        ]

    async def test_fn_with_args(self):
        async def fn(name: str, age: int = 30) -> str:
            return f"Hello, {name}! You're {age} years old."

        prompt = Prompt.from_function(fn)
        assert await prompt.render(arguments=dict(name="World")) == [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text", text="Hello, World! You're 30 years old."
                ),
            )
        ]

    async def test_callable_object(self):
        class MyPrompt:
            def __call__(self, name: str) -> str:
                return f"Hello, {name}!"

        prompt = Prompt.from_function(MyPrompt())
        assert await prompt.render(arguments=dict(name="World")) == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, World!")
            )
        ]

    async def test_async_callable_object(self):
        class MyPrompt:
            async def __call__(self, name: str) -> str:
                return f"Hello, {name}!"

        prompt = Prompt.from_function(MyPrompt())
        assert await prompt.render(arguments=dict(name="World")) == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, World!")
            )
        ]

    async def test_fn_with_invalid_kwargs(self):
        async def fn(name: str, age: int = 30) -> str:
            return f"Hello, {name}! You're {age} years old."

        prompt = Prompt.from_function(fn)
        with pytest.raises(ValueError):
            await prompt.render(arguments=dict(age=40))

    async def test_fn_returns_message(self):
        async def fn() -> PromptMessage:
            return PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, world!")
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, world!")
            )
        ]

    async def test_fn_returns_assistant_message(self):
        async def fn() -> PromptMessage:
            return PromptMessage(
                role="assistant", content=TextContent(type="text", text="Hello, world!")
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="assistant", content=TextContent(type="text", text="Hello, world!")
            )
        ]

    async def test_fn_returns_multiple_messages(self):
        expected = [
            Message(role="user", content="Hello, world!"),
            Message(role="assistant", content="How can I help you today?"),
            Message(
                role="user",
                content="I'm looking for a restaurant in the center of town.",
            ),
        ]

        async def fn() -> list[PromptMessage]:
            return expected

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == expected

    async def test_fn_returns_list_of_strings(self):
        expected = [
            "Hello, world!",
            "I'm looking for a restaurant in the center of town.",
        ]

        async def fn() -> list[str]:
            return expected

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(role="user", content=TextContent(type="text", text=t))
            for t in expected
        ]

    async def test_fn_returns_resource_content(self):
        """Test returning a message with resource content."""

        async def fn() -> PromptMessage:
            return PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )
        ]

    async def test_fn_returns_mixed_content(self):
        """Test returning messages with mixed content types."""

        async def fn() -> list[PromptMessage | str]:
            return [
                "Please analyze this file:",
                PromptMessage(
                    role="user",
                    content=EmbeddedResource(
                        type="resource",
                        resource=TextResourceContents(
                            uri=FileUrl("file://file.txt"),
                            text="File contents",
                            mimeType="text/plain",
                        ),
                    ),
                ),
                Message(role="assistant", content="I'll help analyze that file."),
            ]

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user",
                content=TextContent(type="text", text="Please analyze this file:"),
            ),
            PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            ),
            PromptMessage(
                role="assistant",
                content=TextContent(type="text", text="I'll help analyze that file."),
            ),
        ]

    async def test_fn_returns_message_with_resource(self):
        """Test returning a dict with resource content."""

        async def fn() -> PromptMessage:
            return PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )
        ]


class TestPromptTypeConversion:
    async def test_list_of_integers_as_string_args(self):
        """Test that prompts can handle complex types passed as strings from MCP spec."""

        def sum_numbers(numbers: list[int]) -> str:
            """Calculate the sum of a list of numbers."""
            total = sum(numbers)
            return f"The sum is: {total}"

        prompt = Prompt.from_function(sum_numbers)

        # MCP spec only allows string arguments, so this should work
        # after we implement type conversion
        result_from_string = await prompt.render(
            arguments={"numbers": "[1, 2, 3, 4, 5]"}
        )
        assert result_from_string == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="The sum is: 15")
            )
        ]

        # Both should work now with string conversion
        result_from_list_string = await prompt.render(
            arguments={"numbers": "[1, 2, 3, 4, 5]"}
        )
        assert result_from_list_string == result_from_string

    async def test_various_type_conversions(self):
        """Test type conversion for various data types."""

        def process_data(
            name: str,
            age: int,
            scores: list[float],
            metadata: dict[str, str],
            active: bool,
        ) -> str:
            return f"{name} ({age}): {len(scores)} scores, active={active}, metadata keys={list(metadata.keys())}"

        prompt = Prompt.from_function(process_data)

        # All arguments as strings (as MCP would send them)
        result = await prompt.render(
            arguments={
                "name": "Alice",
                "age": "25",
                "scores": "[1.5, 2.0, 3.5]",
                "metadata": '{"project": "test", "version": "1.0"}',
                "active": "true",
            }
        )

        expected_text = (
            "Alice (25): 3 scores, active=True, metadata keys=['project', 'version']"
        )
        assert result == [
            PromptMessage(
                role="user", content=TextContent(type="text", text=expected_text)
            )
        ]

    async def test_type_conversion_error_handling(self):
        """Test that informative errors are raised for invalid type conversions."""
        from fastmcp.exceptions import PromptError

        def typed_prompt(numbers: list[int]) -> str:
            return f"Got {len(numbers)} numbers"

        prompt = Prompt.from_function(typed_prompt)

        # Test with invalid JSON - should raise PromptError due to exception handling in render()
        with pytest.raises(PromptError) as exc_info:
            await prompt.render(arguments={"numbers": "not valid json"})

        assert f"Error rendering prompt {prompt.name}" in str(exc_info.value)

    async def test_json_parsing_fallback(self):
        """Test that JSON parsing falls back to direct validation when needed."""

        def data_prompt(value: int) -> str:
            return f"Value: {value}"

        prompt = Prompt.from_function(data_prompt)

        # This should work with JSON parsing (integer as string)
        result1 = await prompt.render(arguments={"value": "42"})
        assert result1 == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Value: 42")
            )
        ]

        # This should work with direct validation (already an integer string)
        result2 = await prompt.render(arguments={"value": "123"})
        assert result2 == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Value: 123")
            )
        ]

    async def test_mixed_string_and_typed_args(self):
        """Test mixing string args (no conversion) with typed args (conversion needed)."""

        def mixed_prompt(message: str, count: int) -> str:
            return f"{message} (repeated {count} times)"

        prompt = Prompt.from_function(mixed_prompt)

        result = await prompt.render(
            arguments={
                "message": "Hello world",  # str - no conversion needed
                "count": "3",  # int - conversion needed
            }
        )

        assert result == [
            PromptMessage(
                role="user",
                content=TextContent(type="text", text="Hello world (repeated 3 times)"),
            )
        ]


class TestPromptArgumentDescriptions:
    def test_enhanced_descriptions_for_non_string_types(self):
        """Test that non-string argument types get enhanced descriptions with JSON schema."""

        def analyze_data(
            name: str,
            numbers: list[int],
            metadata: dict[str, str],
            threshold: float,
            active: bool,
        ) -> str:
            """Analyze numerical data."""
            return f"Analyzed {name}"

        prompt = Prompt.from_function(analyze_data)

        assert prompt.arguments is not None
        # Check that string parameter has no schema enhancement
        name_arg = next((arg for arg in prompt.arguments if arg.name == "name"), None)
        assert name_arg is not None
        assert name_arg.description is None  # No enhancement for string types

        # Check that non-string parameters have schema enhancements
        numbers_arg = next(
            (arg for arg in prompt.arguments if arg.name == "numbers"), None
        )
        assert numbers_arg is not None
        assert numbers_arg.description is not None
        assert (
            "Provide as a JSON string matching the following schema:"
            in numbers_arg.description
        )
        assert '{"items":{"type":"integer"},"type":"array"}' in numbers_arg.description

        metadata_arg = next(
            (arg for arg in prompt.arguments if arg.name == "metadata"), None
        )
        assert metadata_arg is not None
        assert metadata_arg.description is not None
        assert (
            "Provide as a JSON string matching the following schema:"
            in metadata_arg.description
        )
        assert (
            '{"additionalProperties":{"type":"string"},"type":"object"}'
            in metadata_arg.description
        )

        threshold_arg = next(
            (arg for arg in prompt.arguments if arg.name == "threshold"), None
        )
        assert threshold_arg is not None
        assert threshold_arg.description is not None
        assert (
            "Provide as a JSON string matching the following schema:"
            in threshold_arg.description
        )
        assert '{"type":"number"}' in threshold_arg.description

        active_arg = next(
            (arg for arg in prompt.arguments if arg.name == "active"), None
        )
        assert active_arg is not None
        assert active_arg.description is not None
        assert (
            "Provide as a JSON string matching the following schema:"
            in active_arg.description
        )
        assert '{"type":"boolean"}' in active_arg.description

    def test_enhanced_descriptions_with_existing_descriptions(self):
        """Test that existing parameter descriptions are preserved with schema appended."""
        from typing import Annotated

        from pydantic import Field

        def documented_prompt(
            numbers: Annotated[
                list[int], Field(description="A list of integers to process")
            ],
        ) -> str:
            """Process numbers."""
            return "processed"

        prompt = Prompt.from_function(documented_prompt)

        assert prompt.arguments is not None
        numbers_arg = next(
            (arg for arg in prompt.arguments if arg.name == "numbers"), None
        )
        assert numbers_arg is not None
        # Should have both the original description and the schema
        assert numbers_arg.description is not None
        assert "A list of integers to process" in numbers_arg.description
        assert "\n\n" in numbers_arg.description  # Should have newline separator
        assert (
            "Provide as a JSON string matching the following schema:"
            in numbers_arg.description
        )

    def test_string_parameters_no_enhancement(self):
        """Test that string parameters don't get schema enhancement."""

        def string_only_prompt(message: str, name: str) -> str:
            return f"{message}, {name}"

        prompt = Prompt.from_function(string_only_prompt)

        assert prompt.arguments is not None
        for arg in prompt.arguments:
            # String parameters should not have schema enhancement
            if arg.description is not None:
                assert (
                    "Provide as a JSON string matching the following schema:"
                    not in arg.description
                )
