from fastmcp import FastMCP
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ToolTransformConfig


async def test_tool_transformation_via_layer():
    """Test that tool transformations work via add_transform(ToolTransform(...))."""
    mcp = FastMCP("Test Server")

    @mcp.tool()
    def echo(message: str) -> str:
        """Echo back the message provided."""
        return message

    mcp.add_transform(
        ToolTransform({"echo": ToolTransformConfig(name="echo_transformed")})
    )

    tools = await mcp.list_tools()
    assert len(tools) == 1
    assert any(t.name == "echo_transformed" for t in tools)
    tool = next(t for t in tools if t.name == "echo_transformed")
    assert tool.name == "echo_transformed"


async def test_transformed_tool_filtering():
    """Test that tool transformations add tags that affect filtering."""
    mcp = FastMCP("Test Server")

    @mcp.tool()
    def echo(message: str) -> str:
        """Echo back the message provided."""
        return message

    # Add transformation that adds tags
    mcp.add_transform(
        ToolTransform(
            {
                "echo": ToolTransformConfig(
                    name="echo_transformed", tags={"enabled_tools"}
                )
            }
        )
    )
    # Enable only tools with the enabled_tools tag
    mcp.enable(tags={"enabled_tools"}, only=True)

    tools = await mcp.list_tools()
    # With transformation applied, the tool now has the enabled_tools tag
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

    mcp.add_transform(
        ToolTransform(
            {"tool_without_annotation": ToolTransformConfig(name="transformed_tool")}
        )
    )

    # Test with client to verify structured output is populated
    async with Client(mcp) as client:
        result = await client.call_tool("transformed_tool", {"message": "test"})

        # Structured output should be populated even without return annotation
        assert result.data is not None
        assert result.data == {"result": "processed", "input": "test"}


async def test_layer_based_transforms():
    """Test that ToolTransform layer works after tool registration."""
    mcp = FastMCP("Test Server")

    @mcp.tool()
    def my_tool() -> str:
        return "hello"

    # Add transform after tool registration
    mcp.add_transform(
        ToolTransform({"my_tool": ToolTransformConfig(name="renamed_tool")})
    )

    tools = await mcp.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "renamed_tool"


async def test_server_level_transforms_apply_to_mounted_servers():
    """Test that server-level transforms apply to tools from mounted servers."""
    main = FastMCP("Main")
    sub = FastMCP("Sub")

    @sub.tool()
    def sub_tool() -> str:
        return "hello from sub"

    main.mount(sub)

    # Add transform for the mounted tool at server level
    main.add_transform(
        ToolTransform({"sub_tool": ToolTransformConfig(name="renamed_sub_tool")})
    )

    tools = await main.list_tools()
    tool_names = [t.name for t in tools]

    assert "renamed_sub_tool" in tool_names
    assert "sub_tool" not in tool_names
