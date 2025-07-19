from fastmcp import FastMCP
from fastmcp.tools.tool_transform import ToolTransformConfig


async def test_tool_transformation_in_tool_manager():
    """Test that tool transformations are applied in the tool manager."""
    mcp = FastMCP("Test Server")

    @mcp.tool()
    def echo(message: str) -> str:
        """Echo back the message provided."""
        return message

    mcp.add_tool_transformation("echo", ToolTransformConfig(name="echo_transformed"))

    tools_dict = await mcp._tool_manager.get_tools()
    tools = list(tools_dict.values())
    assert len(tools) == 1
    assert "echo_transformed" in tools_dict
    assert tools_dict["echo_transformed"].name == "echo_transformed"


async def test_transformed_tool_filtering():
    """Test that tool transformations are applied in the tool manager."""
    mcp = FastMCP("Test Server", include_tags={"enabled_tools"})

    @mcp.tool()
    def echo(message: str) -> str:
        """Echo back the message provided."""
        return message

    tools = list(await mcp._list_tools())
    assert len(tools) == 0

    mcp.add_tool_transformation(
        "echo", ToolTransformConfig(name="echo_transformed", tags={"enabled_tools"})
    )

    tools = list(await mcp._list_tools())
    assert len(tools) == 1
