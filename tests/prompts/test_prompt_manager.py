import pytest

from fastmcp.exceptions import PromptError
from fastmcp.prompts import Prompt
from fastmcp.prompts.prompt import PromptArgument, TextContent, UserMessage
from fastmcp.prompts.prompt_manager import PromptManager


class TestPromptManager:
    def test_add_prompt(self):
        """Test adding a prompt to the manager."""

        def fn() -> str:
            return "Hello, world!"

        manager = PromptManager()
        prompt = Prompt.from_function(fn)
        added = manager.add_prompt(prompt)
        assert added == prompt
        assert manager.get_prompt("fn") == prompt

    def test_add_duplicate_prompt(self, caplog):
        """Test adding the same prompt twice."""

        def fn() -> str:
            return "Hello, world!"

        manager = PromptManager(duplicate_behavior="warn")
        prompt = Prompt.from_function(fn)
        first = manager.add_prompt(prompt)
        second = manager.add_prompt(prompt)
        assert first == second
        assert "Prompt already exists" in caplog.text

    def test_disable_warn_on_duplicate_prompts(self, caplog):
        """Test disabling warning on duplicate prompts."""

        def fn() -> str:
            return "Hello, world!"

        manager = PromptManager(duplicate_behavior="ignore")
        prompt = Prompt.from_function(fn)
        first = manager.add_prompt(prompt)
        second = manager.add_prompt(prompt)
        assert first == second
        assert "Prompt already exists" not in caplog.text

    def test_warn_on_duplicate_prompts(self, caplog):
        """Test warning on duplicate prompts."""
        manager = PromptManager(duplicate_behavior="warn")

        def test_fn() -> str:
            return "Test prompt"

        prompt = Prompt.from_function(test_fn, name="test_prompt")

        manager.add_prompt(prompt)
        manager.add_prompt(prompt)

        assert "Prompt already exists: test_prompt" in caplog.text
        # Should have the prompt
        assert manager.get_prompt("test_prompt") is not None

    def test_error_on_duplicate_prompts(self):
        """Test error on duplicate prompts."""
        manager = PromptManager(duplicate_behavior="error")

        def test_fn() -> str:
            return "Test prompt"

        prompt = Prompt.from_function(test_fn, name="test_prompt")

        manager.add_prompt(prompt)

        with pytest.raises(ValueError, match="Prompt already exists: test_prompt"):
            manager.add_prompt(prompt)

    def test_replace_duplicate_prompts(self):
        """Test replacing duplicate prompts."""
        manager = PromptManager(duplicate_behavior="replace")

        def original_fn() -> str:
            return "Original prompt"

        def replacement_fn() -> str:
            return "Replacement prompt"

        prompt1 = Prompt.from_function(original_fn, name="test_prompt")
        prompt2 = Prompt.from_function(replacement_fn, name="test_prompt")

        manager.add_prompt(prompt1)
        manager.add_prompt(prompt2)

        # Should have replaced with the new prompt
        prompt = manager.get_prompt("test_prompt")
        assert prompt is not None
        assert prompt.fn.__name__ == "replacement_fn"

    def test_ignore_duplicate_prompts(self):
        """Test ignoring duplicate prompts."""
        manager = PromptManager(duplicate_behavior="ignore")

        def original_fn() -> str:
            return "Original prompt"

        def replacement_fn() -> str:
            return "Replacement prompt"

        prompt1 = Prompt.from_function(original_fn, name="test_prompt")
        prompt2 = Prompt.from_function(replacement_fn, name="test_prompt")

        manager.add_prompt(prompt1)
        result = manager.add_prompt(prompt2)

        # Should keep the original
        prompt = manager.get_prompt("test_prompt")
        assert prompt is not None
        assert prompt.fn.__name__ == "original_fn"
        # Result should be the original prompt
        assert result.fn.__name__ == "original_fn"

    def test_list_prompts(self):
        """Test listing all prompts."""

        def fn1() -> str:
            return "Hello, world!"

        def fn2() -> str:
            return "Goodbye, world!"

        manager = PromptManager()
        prompt1 = Prompt.from_function(fn1)
        prompt2 = Prompt.from_function(fn2)
        manager.add_prompt(prompt1)
        manager.add_prompt(prompt2)
        prompts = manager.list_prompts()
        assert len(prompts) == 2
        assert prompts == [prompt1, prompt2]

    @pytest.mark.anyio
    async def test_render_prompt(self):
        """Test rendering a prompt."""

        def fn() -> str:
            return "Hello, world!"

        manager = PromptManager()
        prompt = Prompt.from_function(fn)
        manager.add_prompt(prompt)
        messages = await manager.render_prompt("fn")
        assert messages == [
            UserMessage(content=TextContent(type="text", text="Hello, world!"))
        ]

    @pytest.mark.anyio
    async def test_render_prompt_with_args(self):
        """Test rendering a prompt with arguments."""

        def fn(name: str) -> str:
            return f"Hello, {name}!"

        manager = PromptManager()
        prompt = Prompt.from_function(fn)
        manager.add_prompt(prompt)
        messages = await manager.render_prompt("fn", arguments={"name": "World"})
        assert messages == [
            UserMessage(content=TextContent(type="text", text="Hello, World!"))
        ]

    @pytest.mark.anyio
    async def test_render_unknown_prompt(self):
        """Test rendering a non-existent prompt."""
        manager = PromptManager()
        with pytest.raises(PromptError, match="Unknown prompt: unknown"):
            await manager.render_prompt("unknown")

    @pytest.mark.anyio
    async def test_render_prompt_with_missing_args(self):
        """Test rendering a prompt with missing required arguments."""

        def fn(name: str) -> str:
            return f"Hello, {name}!"

        manager = PromptManager()
        prompt = Prompt.from_function(fn)
        manager.add_prompt(prompt)
        with pytest.raises(ValueError, match="Missing required arguments"):
            await manager.render_prompt("fn")


class TestPromptTags:
    """Test functionality related to prompt tags."""

    def test_add_prompt_with_tags(self):
        """Test adding a prompt with tags."""

        def greeting() -> str:
            return "Hello, world!"

        manager = PromptManager()
        prompt = Prompt.from_function(greeting, tags={"greeting", "simple"})
        manager.add_prompt(prompt)

        prompt = manager.get_prompt("greeting")
        assert prompt is not None
        assert prompt.tags == {"greeting", "simple"}

    def test_add_prompt_with_empty_tags(self):
        """Test adding a prompt with empty tags."""

        def greeting() -> str:
            return "Hello, world!"

        manager = PromptManager()
        prompt = Prompt.from_function(greeting, tags=set())
        manager.add_prompt(prompt)

        prompt = manager.get_prompt("greeting")
        assert prompt is not None
        assert prompt.tags == set()

    def test_add_prompt_with_none_tags(self):
        """Test adding a prompt with None tags."""

        def greeting() -> str:
            return "Hello, world!"

        manager = PromptManager()
        prompt = Prompt.from_function(greeting, tags=None)
        manager.add_prompt(prompt)

        prompt = manager.get_prompt("greeting")
        assert prompt is not None
        assert prompt.tags == set()

    def test_list_prompts_with_tags(self):
        """Test listing prompts with specific tags."""

        def greeting() -> str:
            return "Hello, world!"

        def weather(location: str) -> str:
            return f"Weather for {location}"

        def summary(text: str) -> str:
            return f"Summary of: {text}"

        manager = PromptManager()
        manager.add_prompt(Prompt.from_function(greeting, tags={"greeting", "simple"}))
        manager.add_prompt(Prompt.from_function(weather, tags={"weather", "location"}))
        manager.add_prompt(
            Prompt.from_function(summary, tags={"summary", "nlp", "simple"})
        )

        # Filter prompts by tags
        simple_prompts = [p for p in manager.list_prompts() if "simple" in p.tags]
        assert len(simple_prompts) == 2
        assert {p.name for p in simple_prompts} == {"greeting", "summary"}

        nlp_prompts = [p for p in manager.list_prompts() if "nlp" in p.tags]
        assert len(nlp_prompts) == 1
        assert nlp_prompts[0].name == "summary"

    def test_import_prompts_preserves_tags(self):
        """Test that importing prompts preserves their tags."""
        source_manager = PromptManager()

        def sample_prompt() -> str:
            return "Sample prompt"

        source_manager.add_prompt(
            Prompt.from_function(sample_prompt, tags={"example", "test"})
        )

        target_manager = PromptManager()
        target_manager.import_prompts(source_manager, "imported/")

        imported_prompt = target_manager.get_prompt("imported/sample_prompt")
        assert imported_prompt is not None
        assert imported_prompt.tags == {"example", "test"}


class TestImports:
    def test_import_prompts(self):
        """Test importing prompts from one manager to another with a prefix."""
        # Setup source manager with prompts
        source_manager = PromptManager()

        summary_prompt = Prompt(
            name="summary",
            description="Generate a summary of text",
            arguments=[PromptArgument(name="text", description="Text to summarize")],
            fn=lambda: None,  # type: ignore
        )
        source_manager.add_prompt(summary_prompt)

        translate_prompt = Prompt(
            name="translate",
            description="Translate text to another language",
            arguments=[
                PromptArgument(name="text", description="Text to translate"),
                PromptArgument(name="language", description="Target language"),
            ],
            fn=lambda: None,  # type: ignore
        )
        source_manager.add_prompt(translate_prompt)

        # Create target manager
        target_manager = PromptManager()

        # Import prompts from source to target
        prefix = "nlp/"
        target_manager.import_prompts(source_manager, prefix)

        # Verify prompts were imported with prefixes
        assert "nlp/summary" in target_manager._prompts
        assert "nlp/translate" in target_manager._prompts

        # Verify the original prompts still exist in source manager
        assert "summary" in source_manager._prompts
        assert "translate" in source_manager._prompts

        assert target_manager._prompts["nlp/summary"].fn == summary_prompt.fn
        assert target_manager._prompts["nlp/translate"].fn == translate_prompt.fn

    def test_import_prompts_with_duplicates(self):
        """Test handling of duplicate prompts during import."""
        # Setup source and target managers with same prompt names
        source_manager = PromptManager()
        target_manager = PromptManager()

        source_prompt = Prompt(
            name="common",
            description="Source description",
            arguments=None,
            fn=lambda: None,  # type: ignore
        )
        source_manager._prompts["common"] = source_prompt

        target_prompt = Prompt(
            name="common",
            description="Target description",
            arguments=None,
            fn=lambda: None,  # type: ignore
        )
        target_manager._prompts["common"] = target_prompt

        # Import prompts with prefix
        prefix = "external/"
        target_manager.import_prompts(source_manager, prefix)

        # Verify both prompts exist in target manager
        assert "common" in target_manager._prompts
        assert "external/common" in target_manager._prompts

        assert target_manager._prompts["common"].fn == target_prompt.fn
        assert target_manager._prompts["external/common"].fn == source_prompt.fn

    def test_import_prompts_with_nested_prefixes(self):
        """Test importing already prefixed prompts."""
        # Setup source manager with already prefixed prompts
        first_manager = PromptManager()
        second_manager = PromptManager()
        third_manager = PromptManager()

        original_prompt = Prompt(
            name="analyze",
            description="Analyze text",
            arguments=[PromptArgument(name="text", description="Text to analyze")],
            fn=lambda: None,  # type: ignore
        )
        first_manager._prompts["analyze"] = original_prompt

        # Import to second manager with prefix
        second_manager.import_prompts(first_manager, "text/")

        # Import from second to third with another prefix
        third_manager.import_prompts(second_manager, "ai/")

        # Verify the nested prefixing
        assert "text/analyze" in second_manager._prompts
        assert "ai/text/analyze" in third_manager._prompts

        assert third_manager._prompts["ai/text/analyze"].fn == original_prompt.fn
