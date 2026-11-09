"""Dependencies declared in `Annotated` metadata resolve like `Depends()` defaults."""

from typing import Annotated

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.dependencies import Depends


def get_config() -> dict[str, str]:
    return {"api_url": "https://api.example.com"}


Config = Annotated[dict[str, str], Depends(get_config)]


async def test_tool_resolves_annotated_dependency_outside_its_schema():
    mcp = FastMCP()

    @mcp.tool
    async def fetch(query: str, config: Annotated[dict, Depends(get_config)]) -> str:
        return f"{query} from {config['api_url']}"

    async with Client(mcp) as client:
        (tool,) = await client.list_tools()
        result = await client.call_tool("fetch", {"query": "users"})

    assert list(tool.input_schema["properties"]) == ["query"]
    assert result.data == "users from https://api.example.com"


async def test_type_alias_carries_the_dependency():
    mcp = FastMCP()

    @mcp.tool
    async def fetch(config: Config) -> str:
        return config["api_url"]

    async with Client(mcp) as client:
        (tool,) = await client.list_tools()
        result = await client.call_tool("fetch", {})

    assert tool.input_schema.get("properties", {}) == {}
    assert result.data == "https://api.example.com"


async def test_prompt_and_template_resolve_annotated_dependencies():
    mcp = FastMCP()

    @mcp.prompt
    def describe(topic: str, config: Config) -> str:
        return f"{topic} at {config['api_url']}"

    @mcp.resource("config://{key}")
    def read_config(key: str, config: Config) -> str:
        return config[key]

    async with Client(mcp) as client:
        (prompt,) = await client.list_prompts()
        rendered = await client.get_prompt("describe", {"topic": "search"})
        contents = await client.read_resource("config://api_url")

    assert [argument.name for argument in prompt.arguments or []] == ["topic"]
    assert rendered.messages[0].content.text == "search at https://api.example.com"
    assert contents[0].text == "https://api.example.com"


def test_annotated_dependency_with_a_depends_default_is_rejected():
    mcp = FastMCP()

    with pytest.raises(TypeError):

        @mcp.tool
        async def fetch(
            config: Annotated[dict, Depends(get_config)] = Depends(get_config),
        ) -> str:
            return config["api_url"]
