import pytest
from mcp.types import EmbeddedResource, TextResourceContents
from pydantic import FileUrl

from fastmcp.prompts.base import (
    Message,
    Prompt,
    PromptResult,
)


class TestRenderPrompt:
    async def test_basic_fn(self):
        def fn() -> str:
            return "Hello, world!"

        prompt = Prompt.from_function(fn)
        result = await prompt.render()
        assert result.messages == [Message("Hello, world!")]

    async def test_async_fn(self):
        async def fn() -> str:
            return "Hello, world!"

        prompt = Prompt.from_function(fn)
        result = await prompt.render()
        assert result.messages == [Message("Hello, world!")]

    async def test_fn_with_args(self):
        async def fn(name: str, age: int = 30) -> str:
            return f"Hello, {name}! You're {age} years old."

        prompt = Prompt.from_function(fn)
        result = await prompt.render(arguments=dict(name="World"))
        assert result.messages == [Message("Hello, World! You're 30 years old.")]

    async def test_callable_object(self):
        class MyPrompt:
            def __call__(self, name: str) -> str:
                return f"Hello, {name}!"

        prompt = Prompt.from_function(MyPrompt())
        result = await prompt.render(arguments=dict(name="World"))
        assert result.messages == [Message("Hello, World!")]

    async def test_async_callable_object(self):
        class MyPrompt:
            async def __call__(self, name: str) -> str:
                return f"Hello, {name}!"

        prompt = Prompt.from_function(MyPrompt())
        result = await prompt.render(arguments=dict(name="World"))
        assert result.messages == [Message("Hello, World!")]

    async def test_fn_with_invalid_kwargs(self):
        async def fn(name: str, age: int = 30) -> str:
            return f"Hello, {name}! You're {age} years old."

        prompt = Prompt.from_function(fn)
        with pytest.raises(ValueError):
            await prompt.render(arguments=dict(age=40))

    async def test_fn_returns_message_list(self):
        async def fn() -> list[Message]:
            return [Message("Hello, world!")]

        prompt = Prompt.from_function(fn)
        result = await prompt.render()
        assert result.messages == [Message("Hello, world!")]

    async def test_fn_returns_assistant_message(self):
        async def fn() -> list[Message]:
            return [Message("Hello, world!", role="assistant")]

        prompt = Prompt.from_function(fn)
        result = await prompt.render()
        assert result.messages == [Message("Hello, world!", role="assistant")]

    async def test_fn_returns_multiple_messages(self):
        expected = [
            Message("Hello, world!"),
            Message("How can I help you today?", role="assistant"),
            Message("I'm looking for a restaurant in the center of town."),
        ]

        async def fn() -> list[Message]:
            return expected

        prompt = Prompt.from_function(fn)
        result = await prompt.render()
        assert result.messages == expected

    async def test_fn_returns_list_of_strings(self):
        expected = [
            "Hello, world!",
            "I'm looking for a restaurant in the center of town.",
        ]

        async def fn() -> list[str]:
            return expected

        prompt = Prompt.from_function(fn)
        result = await prompt.render()
        assert result.messages == [Message(t) for t in expected]

    async def test_fn_returns_resource_content(self):
        """Test returning a message with resource content."""

        async def fn() -> list[Message]:
            return [
                Message(
                    content=EmbeddedResource(
                        type="resource",
                        resource=TextResourceContents(
                            uri=FileUrl("file://file.txt"),
                            text="File contents",
                            mimeType="text/plain",
                        ),
                    ),
                    role="user",
                )
            ]

        prompt = Prompt.from_function(fn)
        result = await prompt.render()
        assert result.messages == [
            Message(
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
                role="user",
            )
        ]

    async def test_fn_returns_mixed_content(self):
        """Test returning messages with mixed content types."""

        async def fn() -> list[Message | str]:
            return [
                "Please analyze this file:",
                Message(
                    content=EmbeddedResource(
                        type="resource",
                        resource=TextResourceContents(
                            uri=FileUrl("file://file.txt"),
                            text="File contents",
                            mimeType="text/plain",
                        ),
                    ),
                    role="user",
                ),
                Message("I'll help analyze that file.", role="assistant"),
            ]

        prompt = Prompt.from_function(fn)
        result = await prompt.render()
        assert result.messages == [
            Message("Please analyze this file:"),
            Message(
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
                role="user",
            ),
            Message("I'll help analyze that file.", role="assistant"),
        ]

    async def test_fn_returns_message_with_resource(self):
        """Test returning a message with resource content."""

        async def fn() -> list[Message]:
            return [
                Message(
                    content=EmbeddedResource(
                        type="resource",
                        resource=TextResourceContents(
                            uri=FileUrl("file://file.txt"),
                            text="File contents",
                            mimeType="text/plain",
                        ),
                    ),
                    role="user",
                )
            ]

        prompt = Prompt.from_function(fn)
        result = await prompt.render()
        assert result.messages == [
            Message(
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
                role="user",
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
        assert result_from_string.messages == [Message("The sum is: 15")]

        # Both should work now with string conversion
        result_from_list_string = await prompt.render(
            arguments={"numbers": "[1, 2, 3, 4, 5]"}
        )
        assert result_from_list_string.messages == result_from_string.messages

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
        assert result.messages == [Message(expected_text)]

    async def test_type_conversion_error_handling(self):
        """Test that informative errors are raised for invalid type conversions."""
        from fastmcp.exceptions import PromptError

        def typed_prompt(numbers: list[int]) -> str:
            return f"Got {len(numbers)} numbers"

        prompt = Prompt.from_function(typed_prompt)

        # Test with invalid JSON - should raise PromptError with type conversion details
        with pytest.raises(PromptError) as exc_info:
            await prompt.render(arguments={"numbers": "not valid json"})

        # PromptError passes through unchanged
        assert "Could not convert argument 'numbers'" in str(exc_info.value)
        assert "list[int]" in str(exc_info.value)

    async def test_json_parsing_fallback(self):
        """Test that JSON parsing falls back to direct validation when needed."""

        def data_prompt(value: int) -> str:
            return f"Value: {value}"

        prompt = Prompt.from_function(data_prompt)

        # This should work with JSON parsing (integer as string)
        result1 = await prompt.render(arguments={"value": "42"})
        assert result1.messages == [Message("Value: 42")]

        # This should work with direct validation (already an integer string)
        result2 = await prompt.render(arguments={"value": "123"})
        assert result2.messages == [Message("Value: 123")]

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

        assert result.messages == [Message("Hello world (repeated 3 times)")]


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

    def test_docstring_populates_argument_descriptions(self):
        """Google-style docstrings should populate PromptArgument descriptions."""

        def greet(name: str, topic: str) -> str:
            """Generate a greeting.

            Args:
                name: The person's name.
                topic: The topic to discuss.
            """
            return f"Hello {name}, let's talk about {topic}"

        prompt = Prompt.from_function(greet)

        # Description is summary-only — Args section stripped
        assert prompt.description == "Generate a greeting."

        assert prompt.arguments is not None
        name_arg = next(arg for arg in prompt.arguments if arg.name == "name")
        topic_arg = next(arg for arg in prompt.arguments if arg.name == "topic")
        assert name_arg.description == "The person's name."
        assert topic_arg.description == "The topic to discuss."

    def test_docstring_works_with_numpy_and_sphinx_styles(self):
        def numpy_prompt(a: str) -> str:
            """Do something.

            Parameters
            ----------
            a
                The first argument.
            """
            return a

        def sphinx_prompt(a: str) -> str:
            """Do something.

            :param a: The first argument.
            """
            return a

        numpy = Prompt.from_function(numpy_prompt)
        sphinx = Prompt.from_function(sphinx_prompt)

        for prompt in (numpy, sphinx):
            assert prompt.description == "Do something."
            assert prompt.arguments is not None
            a_arg = next(arg for arg in prompt.arguments if arg.name == "a")
            assert a_arg.description == "The first argument."

    def test_explicit_field_description_overrides_docstring(self):
        """Field(description=...) takes precedence over docstring."""
        from typing import Annotated

        from pydantic import Field

        def greet(
            name: Annotated[str, Field(description="From Field")],
        ) -> str:
            """Greet.

            Args:
                name: From docstring (ignored).
            """
            return f"Hello {name}"

        prompt = Prompt.from_function(greet)
        assert prompt.arguments is not None
        name_arg = next(arg for arg in prompt.arguments if arg.name == "name")
        # Field description wins over the docstring's "From docstring (ignored)".
        # (The existing schema-hint suffix for Annotated params is appended
        # afterwards and is unrelated to precedence.)
        assert name_arg.description is not None
        assert name_arg.description.startswith("From Field")
        assert "From docstring" not in name_arg.description

    def test_explicit_description_keeps_docstring_arg_descriptions(self):
        """Overriding the prompt description does not drop docstring-sourced
        argument descriptions — they come from separate parsing paths."""

        def greet(name: str) -> str:
            """Greet.

            Args:
                name: The person's name.
            """
            return f"Hello {name}"

        prompt = Prompt.from_function(greet, description="Custom description")
        assert prompt.description == "Custom description"
        assert prompt.arguments is not None
        name_arg = next(arg for arg in prompt.arguments if arg.name == "name")
        assert name_arg.description == "The person's name."

    def test_docstring_without_args_section(self):
        """Summary-only docstrings produce a description with no arg descriptions."""

        def greet(name: str) -> str:
            """Just a summary."""
            return f"Hello {name}"

        prompt = Prompt.from_function(greet)
        assert prompt.description == "Just a summary."
        assert prompt.arguments is not None
        name_arg = next(arg for arg in prompt.arguments if arg.name == "name")
        assert name_arg.description is None

    def test_callable_class_sources_description_from_class(self):
        """Class docstring drives the prompt description, while __call__'s
        Args section drives per-argument descriptions (since the arguments
        are __call__'s, not the class's)."""

        class MyPrompt:
            """Class-level description."""

            def __call__(self, name: str) -> str:
                """Internal call doc.

                Args:
                    name: From call.
                """
                return f"Hello {name}"

        prompt = Prompt.from_function(MyPrompt())
        assert prompt.description == "Class-level description."
        assert prompt.arguments is not None
        name_arg = next(arg for arg in prompt.arguments if arg.name == "name")
        assert name_arg.description == "From call."

    def test_prompt_meta_parameter(self):
        """Test that meta parameter is properly handled."""

        def test_prompt(message: str) -> str:
            return f"Response: {message}"

        meta_data = {"version": "3.0", "type": "prompt"}
        prompt = Prompt.from_function(test_prompt, meta=meta_data)

        assert prompt.meta == meta_data
        mcp_prompt = prompt.to_mcp_prompt()
        # MCP prompt includes fastmcp meta, so check that our meta is included
        assert mcp_prompt.meta is not None
        assert meta_data.items() <= mcp_prompt.meta.items()


class TestMessage:
    def test_message_string_content(self):
        """Test Message with string content."""
        from mcp.types import TextContent

        msg = Message("Hello, world!")
        assert msg.role == "user"
        assert isinstance(msg.content, TextContent)
        assert msg.content.text == "Hello, world!"

    def test_message_with_role(self):
        """Test Message with explicit role."""
        from mcp.types import TextContent

        msg = Message("I can help.", role="assistant")
        assert msg.role == "assistant"
        assert isinstance(msg.content, TextContent)
        assert msg.content.text == "I can help."

    def test_message_auto_serializes_dict(self):
        """Test Message auto-serializes dicts to JSON."""
        from mcp.types import TextContent

        msg = Message({"key": "value", "nested": {"a": 1}})
        assert msg.role == "user"
        assert isinstance(msg.content, TextContent)
        assert '"key"' in msg.content.text
        assert '"value"' in msg.content.text

    def test_message_auto_serializes_list(self):
        """Test Message auto-serializes lists to JSON."""
        from mcp.types import TextContent

        msg = Message(["item1", "item2", "item3"])
        assert isinstance(msg.content, TextContent)
        assert '["item1"' in msg.content.text

    def test_message_to_mcp_prompt_message(self):
        """Test conversion to MCP PromptMessage."""
        from mcp.types import TextContent

        msg = Message("Hello", role="assistant")
        mcp_msg = msg.to_mcp_prompt_message()
        assert mcp_msg.role == "assistant"
        assert isinstance(mcp_msg.content, TextContent)
        assert mcp_msg.content.text == "Hello"

    def test_message_passthrough_image_content(self):
        """Test Message passes through ImageContent without JSON serialization."""
        from mcp.types import ImageContent

        img = ImageContent(type="image", data="base64data", mimeType="image/png")
        msg = Message(img, role="user")
        assert isinstance(msg.content, ImageContent)
        assert msg.content.data == "base64data"
        assert msg.content.mimeType == "image/png"

    def test_message_passthrough_audio_content(self):
        """Test Message passes through AudioContent without JSON serialization."""
        from mcp.types import AudioContent

        audio = AudioContent(type="audio", data="base64audio", mimeType="audio/wav")
        msg = Message(audio, role="user")
        assert isinstance(msg.content, AudioContent)
        assert msg.content.data == "base64audio"
        assert msg.content.mimeType == "audio/wav"

    def test_message_image_content_to_mcp_prompt_message(self):
        """Test that ImageContent round-trips through to_mcp_prompt_message."""
        from mcp.types import ImageContent

        img = ImageContent(type="image", data="base64data", mimeType="image/png")
        msg = Message(img, role="user")
        mcp_msg = msg.to_mcp_prompt_message()
        assert isinstance(mcp_msg.content, ImageContent)
        assert mcp_msg.content.data == "base64data"


class TestPromptResult:
    def test_promptresult_from_string(self):
        """Test PromptResult accepts string and wraps as Message."""
        from mcp.types import TextContent

        result = PromptResult("Hello!")
        assert len(result.messages) == 1
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Hello!"
        assert result.messages[0].role == "user"

    def test_promptresult_from_message_list(self):
        """Test PromptResult accepts list of Messages."""
        result = PromptResult(
            [
                Message("Question?"),
                Message("Answer.", role="assistant"),
            ]
        )
        assert len(result.messages) == 2
        assert result.messages[0].role == "user"
        assert result.messages[1].role == "assistant"

    def test_promptresult_rejects_single_message(self):
        """Test PromptResult rejects single Message (must be in list)."""
        with pytest.raises(TypeError, match="must be str or list"):
            PromptResult(Message("Hello"))  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

    def test_promptresult_rejects_dict(self):
        """Test PromptResult rejects dict."""
        with pytest.raises(TypeError, match="must be str or list"):
            PromptResult({"key": "value"})  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

    def test_promptresult_with_meta(self):
        """Test PromptResult with meta field."""
        result = PromptResult(
            "Hello!", meta={"priority": "high", "category": "greeting"}
        )
        assert result.meta == {"priority": "high", "category": "greeting"}

    def test_promptresult_with_description(self):
        """Test PromptResult with description field."""
        result = PromptResult("Hello!", description="A greeting prompt")
        assert result.description == "A greeting prompt"

    def test_promptresult_to_mcp(self):
        """Test conversion to MCP GetPromptResult."""
        result = PromptResult(
            [Message("Hello"), Message("World", role="assistant")],
            description="Test",
            meta={"key": "value"},
        )
        mcp_result = result.to_mcp_prompt_result()
        assert len(mcp_result.messages) == 2
        assert mcp_result.description == "Test"
        assert mcp_result.meta == {"key": "value"}


class TestPromptFieldDefaults:
    """Test prompts with Field() defaults."""

    async def test_field_with_default(self):
        """Test that Field(default=...) correctly provides default values."""

        from pydantic import Field

        def prompt_with_defaults(
            required: str = Field(description="Required parameter"),
            optional: str = Field(
                default="default_value", description="Optional parameter"
            ),
        ) -> str:
            return f"required={required}, optional={optional}"

        prompt = Prompt.from_function(prompt_with_defaults)
        result = await prompt.render(arguments={"required": "test"})
        assert result.messages == [Message("required=test, optional=default_value")]

    async def test_annotated_field_with_default_in_signature(self):
        """Test that Annotated[type, Field(...)] with default in signature works."""
        from typing import Annotated

        from pydantic import Field

        def prompt_with_annotated(
            required: Annotated[str, Field(description="Required parameter")],
            optional: Annotated[
                str, Field(description="Optional parameter")
            ] = "default_value",
        ) -> str:
            return f"required={required}, optional={optional}"

        prompt = Prompt.from_function(prompt_with_annotated)
        result = await prompt.render(arguments={"required": "test"})
        assert result.messages == [Message("required=test, optional=default_value")]

    async def test_multiple_field_defaults(self):
        """Test multiple parameters with Field() defaults."""
        from pydantic import Field

        def prompt_with_multiple_defaults(
            name: str = Field(description="Name"),
            greeting: str = Field(default="Hello", description="Greeting"),
            punctuation: str = Field(default="!", description="Punctuation"),
        ) -> str:
            return f"{greeting}, {name}{punctuation}"

        prompt = Prompt.from_function(prompt_with_multiple_defaults)

        # Test with only required parameter
        result1 = await prompt.render(arguments={"name": "World"})
        assert result1.messages == [Message("Hello, World!")]

        # Test overriding one default
        result2 = await prompt.render(arguments={"name": "World", "greeting": "Hi"})
        assert result2.messages == [Message("Hi, World!")]

        # Test overriding all defaults
        result3 = await prompt.render(
            arguments={"name": "World", "greeting": "Greetings", "punctuation": "."}
        )
        assert result3.messages == [Message("Greetings, World.")]

    async def test_field_defaults_with_type_conversion(self):
        """Test Field() defaults work with type conversion for non-string types."""
        from pydantic import Field

        def prompt_with_typed_defaults(
            count: int = Field(description="Count"),
            multiplier: int = Field(default=2, description="Multiplier"),
        ) -> str:
            return f"result={count * multiplier}"

        prompt = Prompt.from_function(prompt_with_typed_defaults)

        # Pass count as string (MCP requirement), should use default for multiplier
        result = await prompt.render(arguments={"count": "5"})
        assert result.messages == [Message("result=10")]


class TestPromptCallableAndConcurrency:
    """Test prompts with callable objects and concurrent execution."""

    async def test_callable_object_sync(self):
        """Test that callable objects with sync __call__ work."""

        class MyPrompt:
            def __init__(self, greeting: str):
                self.greeting = greeting

            def __call__(self) -> str:
                return f"{self.greeting}, world!"

        prompt = Prompt.from_function(MyPrompt("Hello"))
        result = await prompt.render()
        assert result.messages == [Message("Hello, world!")]

    async def test_callable_object_async(self):
        """Test that callable objects with async __call__ work."""

        class AsyncPrompt:
            def __init__(self, greeting: str):
                self.greeting = greeting

            async def __call__(self) -> str:
                return f"async {self.greeting}!"

        prompt = Prompt.from_function(AsyncPrompt("Hello"))
        result = await prompt.render()
        assert result.messages == [Message("async Hello!")]

    async def test_sync_prompt_runs_concurrently(self):
        """Test that sync prompts run in threadpool and don't block each other."""
        import asyncio
        import threading

        num_calls = 3
        barrier = threading.Barrier(num_calls, timeout=0.5)

        def concurrent_prompt() -> str:
            barrier.wait()
            return "done"

        prompt = Prompt.from_function(concurrent_prompt)

        # Run concurrent renders - will raise BrokenBarrierError if not concurrent
        results = await asyncio.gather(
            prompt.render(),
            prompt.render(),
            prompt.render(),
        )
        assert all(r.messages == [Message("done")] for r in results)
