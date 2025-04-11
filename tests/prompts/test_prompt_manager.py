import pytest

from fastmcp.prompts import Prompt
from fastmcp.prompts.base import PromptArgument, TextContent, UserMessage
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

        manager = PromptManager()
        prompt = Prompt.from_function(fn)
        first = manager.add_prompt(prompt)
        second = manager.add_prompt(prompt)
        assert first == second
        assert "Prompt already exists" in caplog.text

    def test_disable_warn_on_duplicate_prompts(self, caplog):
        """Test disabling warning on duplicate prompts."""

        def fn() -> str:
            return "Hello, world!"

        manager = PromptManager(warn_on_duplicate_prompts=False)
        prompt = Prompt.from_function(fn)
        first = manager.add_prompt(prompt)
        second = manager.add_prompt(prompt)
        assert first == second
        assert "Prompt already exists" not in caplog.text

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
        with pytest.raises(ValueError, match="Unknown prompt: unknown"):
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


class TestImports:
    def test_import_prompts(self):
        """Test importing prompts from one manager to another with a prefix."""
        # Setup source manager with prompts
        source_manager = PromptManager()

        # Create test prompts with proper function handlers
        async def summary_fn(**kwargs):
            return [
                {"role": "assistant", "content": f"Summary of: {kwargs.get('text')}"}
            ]

        async def translate_fn(**kwargs):
            return [
                {
                    "role": "assistant",
                    "content": f"Translation to {kwargs.get('language')}: {kwargs.get('text')}",
                }
            ]

        summary_prompt = Prompt(
            name="summary",
            description="Generate a summary of text",
            arguments=[PromptArgument(name="text", description="Text to summarize")],
            fn=summary_fn,
        )
        source_manager._prompts["summary"] = summary_prompt

        translate_prompt = Prompt(
            name="translate",
            description="Translate text to another language",
            arguments=[
                PromptArgument(name="text", description="Text to translate"),
                PromptArgument(name="language", description="Target language"),
            ],
            fn=translate_fn,
        )
        source_manager._prompts["translate"] = translate_prompt

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

        # Verify the imported prompts have the correct properties
        assert target_manager._prompts["nlp/summary"].name == "summary"
        assert (
            target_manager._prompts["nlp/summary"].description
            == "Generate a summary of text"
        )

        assert target_manager._prompts["nlp/translate"].name == "translate"
        assert (
            target_manager._prompts["nlp/translate"].description
            == "Translate text to another language"
        )

        # Verify functions were properly copied
        if hasattr(target_manager._prompts["nlp/summary"], "fn"):
            assert (
                target_manager._prompts["nlp/summary"].fn.__name__
                == summary_fn.__name__
            )

        if hasattr(target_manager._prompts["nlp/translate"], "fn"):
            assert (
                target_manager._prompts["nlp/translate"].fn.__name__
                == translate_fn.__name__
            )

    def test_import_prompts_with_duplicates(self):
        """Test handling of duplicate prompts during import."""
        # Setup source and target managers with same prompt names
        source_manager = PromptManager()
        target_manager = PromptManager()

        # Add the same prompt name to both managers with functions
        async def source_fn(**kwargs):
            return [{"role": "assistant", "content": "Source content"}]

        async def target_fn(**kwargs):
            return [{"role": "assistant", "content": "Target content"}]

        source_prompt = Prompt(
            name="common",
            description="Source description",
            arguments=None,
            fn=source_fn,
        )
        source_manager._prompts["common"] = source_prompt

        target_prompt = Prompt(
            name="common",
            description="Target description",
            arguments=None,
            fn=target_fn,
        )
        target_manager._prompts["common"] = target_prompt

        # Import prompts with prefix
        prefix = "external/"
        target_manager.import_prompts(source_manager, prefix)

        # Verify both prompts exist in target manager
        assert "common" in target_manager._prompts
        assert "external/common" in target_manager._prompts

        # Verify the functions of both prompts
        if hasattr(target_manager._prompts["common"], "fn") and hasattr(
            target_manager._prompts["external/common"], "fn"
        ):
            assert target_manager._prompts["common"].fn.__name__ == target_fn.__name__
            assert (
                target_manager._prompts["external/common"].fn.__name__
                == source_fn.__name__
            )

    def test_import_prompts_with_nested_prefixes(self):
        """Test importing already prefixed prompts."""
        # Setup source manager with already prefixed prompts
        first_manager = PromptManager()
        second_manager = PromptManager()
        third_manager = PromptManager()

        # Add prompt to first manager with a function
        async def analyze_fn(**kwargs):
            return [
                {"role": "assistant", "content": f"Analysis of: {kwargs.get('text')}"}
            ]

        original_prompt = Prompt(
            name="analyze",
            description="Analyze text",
            arguments=[PromptArgument(name="text", description="Text to analyze")],
            fn=analyze_fn,
        )
        first_manager._prompts["analyze"] = original_prompt

        # Import to second manager with prefix
        second_manager.import_prompts(first_manager, "text/")

        # Import from second to third with another prefix
        third_manager.import_prompts(second_manager, "ai/")

        # Verify the nested prefixing
        assert "text/analyze" in second_manager._prompts
        assert "ai/text/analyze" in third_manager._prompts

        # Verify the properties of the most nested prompt
        assert third_manager._prompts["ai/text/analyze"].name == "analyze"
        assert third_manager._prompts["ai/text/analyze"].description == "Analyze text"

        # Verify function was properly copied through multiple imports
        if hasattr(third_manager._prompts["ai/text/analyze"], "fn"):
            assert (
                third_manager._prompts["ai/text/analyze"].fn.__name__
                == analyze_fn.__name__
            )
