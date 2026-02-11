import httpx

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import (
    ArgTransformConfig,
    ToolTransformConfig,
)


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


async def test_tool_transform_config_enabled_false_hides_tool():
    """Test that ToolTransformConfig with enabled=False hides the tool from list_tools."""
    mcp = FastMCP("Test Server")

    @mcp.tool()
    def visible_tool() -> str:
        return "visible"

    @mcp.tool()
    def hidden_tool() -> str:
        return "hidden"

    # Disable one tool via transformation
    mcp.add_transform(
        ToolTransform({"hidden_tool": ToolTransformConfig(enabled=False)})
    )

    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]

    assert "visible_tool" in tool_names
    assert "hidden_tool" not in tool_names


async def test_tool_transform_config_enabled_false_with_rename():
    """Test that enabled=False works together with other transformations like rename."""
    mcp = FastMCP("Test Server")

    @mcp.tool()
    def my_tool() -> str:
        return "result"

    # Rename AND disable
    mcp.add_transform(
        ToolTransform(
            {"my_tool": ToolTransformConfig(name="renamed_and_disabled", enabled=False)}
        )
    )

    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]

    # Tool should be hidden regardless of rename
    assert "my_tool" not in tool_names
    assert "renamed_and_disabled" not in tool_names


async def test_tool_transform_config_enabled_true_keeps_tool_visible():
    """Test that ToolTransformConfig with enabled=True (explicit) keeps the tool visible."""
    mcp = FastMCP("Test Server")

    @mcp.tool()
    def my_tool() -> str:
        return "result"

    # Explicitly set enabled=True (should be same as default)
    mcp.add_transform(ToolTransform({"my_tool": ToolTransformConfig(enabled=True)}))

    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]

    assert "my_tool" in tool_names


async def test_tool_transform_config_enabled_true_overrides_earlier_disable():
    """Test that ToolTransformConfig with enabled=True can re-enable a previously disabled tool."""
    mcp = FastMCP("Test Server")

    @mcp.tool()
    def my_tool() -> str:
        return "result"

    # Disable the tool first
    mcp.disable(names={"my_tool"})

    # Verify tool is initially hidden
    tools = await mcp.list_tools()
    assert "my_tool" not in [t.name for t in tools]

    # Re-enable via transformation (later transforms win)
    mcp.add_transform(ToolTransform({"my_tool": ToolTransformConfig(enabled=True)}))

    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]

    # Tool should now be visible
    assert "my_tool" in tool_names


async def test_openapi_path_params_not_duplicated_in_description():
    """Path parameter details should live in inputSchema, not the description.

    Regression test for https://github.com/jlowin/fastmcp/issues/3130 — hiding
    a path param via ToolTransform left stale references in the description
    because the description was generated before transforms ran. The fix is to
    keep parameter docs in inputSchema only, where transforms can control them.
    """
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "0.1.0"},
        "paths": {
            "/api/{version}/users/{user_id}": {
                "get": {
                    "operationId": "my_endpoint",
                    "summary": "My endpoint",
                    "parameters": [
                        {
                            "name": "version",
                            "in": "path",
                            "required": True,
                            "description": "API version",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "description": "The user ID",
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    }

    async with httpx.AsyncClient(base_url="http://localhost") as http_client:
        mcp = FastMCP.from_openapi(openapi_spec=spec, client=http_client)

        # Hide one of the two path params
        mcp.add_transform(
            ToolTransform(
                {
                    "my_endpoint": ToolTransformConfig(
                        arguments={
                            "version": ArgTransformConfig(hide=True, default="v1"),
                        }
                    )
                }
            )
        )

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = tools[0]

            # Description should be the summary only — no parameter details
            assert tool.description == "My endpoint"

            # Hidden param gone from schema, visible param still present
            assert "version" not in tool.inputSchema.get("properties", {})
            assert "user_id" in tool.inputSchema["properties"]
            assert (
                tool.inputSchema["properties"]["user_id"]["description"]
                == "The user ID"
            )
