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

    tools = list(await mcp._list_tools_middleware())
    assert len(tools) == 0

    mcp.add_tool_transformation(
        "echo", ToolTransformConfig(name="echo_transformed", tags={"enabled_tools"})
    )

    tools = list(await mcp._list_tools_middleware())
    assert len(tools) == 1


async def test_transformed_tool_structured_output_without_annotation():
    """Test that transformed tools generate structured output when original tool has no return annotation.

    Ref: https://github.com/jlowin/fastmcp/issues/1369
    """
    from fastmcp.client import Client

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def tool_without_annotation(message: str):  # No return annotation
        """A tool without return type annotation."""
        return {"result": "processed", "input": message}

    # Create a transformed tool
    mcp.add_tool_transformation(
        "tool_without_annotation", ToolTransformConfig(name="transformed_tool")
    )

    # Test with client to verify structured output is populated
    async with Client(mcp) as client:
        result = await client.call_tool("transformed_tool", {"message": "test"})

        # Structured output should be populated even without return annotation
        assert result.data is not None
        assert result.data == {"result": "processed", "input": "test"}
