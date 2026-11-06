import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.transforms import PromptsAsTools, ResourcesAsTools


@pytest.mark.parametrize(
    ("transform_type", "name", "description", "arguments"),
    [
        (ResourcesAsTools, "list_resources", "user list resources", {}),
        (
            ResourcesAsTools,
            "read_resource",
            "user read resource",
            {"uri": "test://one"},
        ),
        (PromptsAsTools, "list_prompts", "user list prompts", {}),
        (PromptsAsTools, "get_prompt", "user get prompt", {"name": "greet"}),
    ],
)
async def test_user_tool_collision_lists_and_dispatches_user_tool(
    transform_type, name: str, description: str, arguments: dict[str, str]
) -> None:
    server = FastMCP("collision")

    @server.tool(name=name, description=description)
    def user_tool(
        uri: str = "", name: str = "", arguments: dict[str, str] | None = None
    ) -> str:
        return "user tool result"

    server.add_transform(transform_type(server))

    async with Client(server) as client:
        listed = {tool.name: tool for tool in await client.list_tools()}
        assert listed[name].description == description
        result = await client.call_tool(name, arguments)

    assert result.data == "user tool result"


@pytest.mark.parametrize(
    ("transform_type", "name", "arguments", "expected"),
    [
        (ResourcesAsTools, "list_resources", {}, "test://one"),
        (ResourcesAsTools, "read_resource", {"uri": "test://one"}, "resource data"),
        (PromptsAsTools, "list_prompts", {}, "greet"),
        (PromptsAsTools, "get_prompt", {"name": "greet"}, "hello"),
    ],
)
async def test_generated_adapter_remains_when_colliding_user_tool_disabled(
    transform_type, name: str, arguments: dict[str, str], expected: str
) -> None:
    server = FastMCP("collision")

    @server.tool(name=name, tags={"user"})
    def user_tool(
        uri: str = "", name: str = "", arguments: dict[str, str] | None = None
    ) -> str:
        return "user tool result"

    @server.resource("test://one")
    def resource() -> str:
        return "resource data"

    @server.prompt
    def greet() -> str:
        return "hello"

    server.add_transform(transform_type(server))
    server.disable(tags={"user"})

    async with Client(server) as client:
        listed = {tool.name for tool in await client.list_tools()}
        assert name in listed
        result = await client.call_tool(name, arguments)

    assert "user tool result" not in result.data
    assert expected in result.data
