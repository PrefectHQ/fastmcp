"""Tests for the Choice provider."""

from fastmcp import FastMCP
from fastmcp.apps.choice import Choice


class TestChoiceProvider:
    async def test_choose_returns_structured_content(self):
        server = FastMCP("test", providers=[Choice()])

        result = await server.call_tool(
            "choose",
            {"prompt": "Pick one", "options": ["A", "B", "C"]},
        )
        assert result.structured_content is not None

    async def test_tool_visible_to_model(self):
        server = FastMCP("test", providers=[Choice()])

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "choose" in tool_names

    async def test_custom_name(self):
        server = FastMCP("test", providers=[Choice(name="Picker")])

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "choose" in tool_names

    async def test_custom_title(self):
        server = FastMCP("test", providers=[Choice(title="Select Strategy")])

        result = await server.call_tool(
            "choose",
            {"prompt": "How?", "options": ["Fast", "Slow"]},
        )
        assert result.structured_content is not None

    async def test_many_options(self):
        server = FastMCP("test", providers=[Choice()])

        result = await server.call_tool(
            "choose",
            {
                "prompt": "Pick a color",
                "options": ["Red", "Blue", "Green", "Yellow", "Purple"],
            },
        )
        assert result.structured_content is not None
