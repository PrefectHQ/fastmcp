"""Tests for prompt mounting."""

from fastmcp import FastMCP


class TestPrompts:
    """Test mounting with prompts."""

    async def test_mount_with_prompts(self):
        """Test mounting a server with prompts."""
        main_app = FastMCP("MainApp")
        assistant_app = FastMCP("AssistantApp")

        @assistant_app.prompt
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        # Mount the assistant app
        main_app.mount(assistant_app, "assistant")

        # Prompt should be accessible through main app
        prompts = await main_app.list_prompts()
        assert any(p.name == "assistant_greeting" for p in prompts)

        # Render the prompt
        result = await main_app.render_prompt("assistant_greeting", {"name": "World"})
        assert result.messages is not None
        # The message should contain our greeting text

    async def test_adding_prompt_after_mounting(self):
        """Test adding a prompt after mounting."""
        main_app = FastMCP("MainApp")
        assistant_app = FastMCP("AssistantApp")

        # Mount the assistant app before adding prompts
        main_app.mount(assistant_app, "assistant")

        # Add a prompt after mounting
        @assistant_app.prompt
        def farewell(name: str) -> str:
            return f"Goodbye, {name}!"

        # Prompt should be accessible through main app
        prompts = await main_app.list_prompts()
        assert any(p.name == "assistant_farewell" for p in prompts)

        # Render the prompt
        result = await main_app.render_prompt("assistant_farewell", {"name": "World"})
        assert result.messages is not None
        # The message should contain our farewell text
