from typing import Any

import mcp.types as mcp_types
from mcp.types import Tool as MCPTool
from mcp.types import ToolAnnotations, ToolExecution

from fastmcp import Client, FastMCP
from fastmcp.tools.tool import Tool


async def test_tool_annotations_in_tool_manager():
    """Test that tool annotations are correctly stored in the tool manager."""
    mcp = FastMCP("Test Server")

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Echo Tool",
            readOnlyHint=True,
            openWorldHint=False,
        )
    )
    def echo(message: str) -> str:
        """Echo back the message provided."""
        return message

    # Check internal tool objects directly
    tools = await mcp.list_tools()
    assert len(tools) == 1
    assert tools[0].annotations is not None
    assert tools[0].annotations.title == "Echo Tool"
    assert tools[0].annotations.readOnlyHint is True
    assert tools[0].annotations.openWorldHint is False


async def test_tool_annotations_in_mcp_protocol():
    """Test that tool annotations are correctly propagated to MCP tools list."""
    mcp = FastMCP("Test Server")

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Echo Tool",
            readOnlyHint=True,
            openWorldHint=False,
        )
    )
    def echo(message: str) -> str:
        """Echo back the message provided."""
        return message

    # Check via MCP protocol
    result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
    assert len(result.tools) == 1
    assert result.tools[0].annotations is not None
    assert result.tools[0].annotations.title == "Echo Tool"
    assert result.tools[0].annotations.readOnlyHint is True
    assert result.tools[0].annotations.openWorldHint is False


async def test_tool_annotations_in_client_api():
    """Test that tool annotations are correctly accessible via client API."""
    mcp = FastMCP("Test Server")

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Echo Tool",
            readOnlyHint=True,
            openWorldHint=False,
        )
    )
    def echo(message: str) -> str:
        """Echo back the message provided."""
        return message

    # Check via client API
    async with Client(mcp) as client:
        tools_result = await client.list_tools()
        assert len(tools_result) == 1
        assert tools_result[0].name == "echo"
        assert tools_result[0].annotations is not None
        assert tools_result[0].annotations.title == "Echo Tool"
        assert tools_result[0].annotations.readOnlyHint is True
        assert tools_result[0].annotations.openWorldHint is False


async def test_provide_tool_annotations_as_dict_to_decorator():
    """Test that tool annotations are correctly accessible via client API."""
    mcp = FastMCP("Test Server")

    @mcp.tool(
        annotations={
            "title": "Echo Tool",
            "readOnlyHint": True,
            "openWorldHint": False,
        }
    )
    def echo(message: str) -> str:
        """Echo back the message provided."""
        return message

    # Check via client API
    async with Client(mcp) as client:
        tools_result = await client.list_tools()
        assert len(tools_result) == 1
        assert tools_result[0].name == "echo"
        assert tools_result[0].annotations is not None
        assert tools_result[0].annotations.title == "Echo Tool"
        assert tools_result[0].annotations.readOnlyHint is True
        assert tools_result[0].annotations.openWorldHint is False


async def test_direct_tool_annotations_in_tool_manager():
    """Test direct ToolAnnotations object is correctly stored in tool manager."""
    mcp = FastMCP("Test Server")

    annotations = ToolAnnotations(
        title="Direct Tool",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )

    @mcp.tool(annotations=annotations)
    def modify(data: dict[str, Any]) -> dict[str, Any]:
        """Modify the data provided."""
        return {"modified": True, **data}

    # Check internal tool objects directly
    tools = await mcp.list_tools()
    assert len(tools) == 1
    assert tools[0].annotations is not None
    assert tools[0].annotations.title == "Direct Tool"
    assert tools[0].annotations.readOnlyHint is False
    assert tools[0].annotations.destructiveHint is True
    assert tools[0].annotations.idempotentHint is False
    assert tools[0].annotations.openWorldHint is True


async def test_direct_tool_annotations_in_client_api():
    """Test direct ToolAnnotations object is correctly accessible via client API."""
    mcp = FastMCP("Test Server")

    annotations = ToolAnnotations(
        title="Direct Tool",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )

    @mcp.tool(annotations=annotations)
    def modify(data: dict[str, Any]) -> dict[str, Any]:
        """Modify the data provided."""
        return {"modified": True, **data}

    # Check via client API
    async with Client(mcp) as client:
        tools_result = await client.list_tools()
        assert len(tools_result) == 1
        assert tools_result[0].name == "modify"
        assert tools_result[0].annotations is not None
        assert tools_result[0].annotations.title == "Direct Tool"
        assert tools_result[0].annotations.readOnlyHint is False
        assert tools_result[0].annotations.destructiveHint is True


async def test_add_tool_method_annotations():
    """Test that tool annotations work with add_tool method."""
    mcp = FastMCP("Test Server")

    def create_item(name: str, value: int) -> dict[str, Any]:
        """Create a new item."""
        return {"name": name, "value": value}

    tool = Tool.from_function(
        create_item,
        name="create_item",
        annotations=ToolAnnotations(
            title="Create Item",
            readOnlyHint=False,
            destructiveHint=False,
        ),
    )

    mcp.add_tool(tool)

    # Check internal tool objects directly
    tools = await mcp.list_tools()
    assert len(tools) == 1
    assert tools[0].annotations is not None
    assert tools[0].annotations.title == "Create Item"
    assert tools[0].annotations.readOnlyHint is False
    assert tools[0].annotations.destructiveHint is False


async def test_tool_functionality_with_annotations():
    """Test that tool functionality is preserved when using annotations."""
    mcp = FastMCP("Test Server")

    def create_item(name: str, value: int) -> dict[str, Any]:
        """Create a new item."""
        return {"name": name, "value": value}

    tool = Tool.from_function(
        create_item,
        name="create_item",
        annotations=ToolAnnotations(
            title="Create Item",
            readOnlyHint=False,
            destructiveHint=False,
        ),
    )
    mcp.add_tool(tool)

    # Use the tool to verify functionality is preserved
    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_item", {"name": "test_item", "value": 42}
        )
        assert result.data == {"name": "test_item", "value": 42}


async def test_task_execution_auto_populated_for_task_enabled_tool():
    """Test that execution.taskSupport is automatically set when tool has task=True."""
    mcp = FastMCP("Test Server")

    @mcp.tool(task=True)
    async def background_tool(data: str) -> str:
        """A tool that runs in background."""
        return f"Processed: {data}"

    async with Client(mcp) as client:
        tools_result = await client.list_tools()
        assert len(tools_result) == 1
        assert tools_result[0].name == "background_tool"
        assert isinstance(tools_result[0], MCPTool)
        assert isinstance(tools_result[0].execution, ToolExecution)
        assert tools_result[0].execution.taskSupport == "optional"


async def test_task_execution_omitted_for_task_disabled_tool():
    """Test that execution is not set when tool has task=False or default."""
    mcp = FastMCP("Test Server")

    @mcp.tool(task=False)
    def sync_tool(data: str) -> str:
        """A synchronous tool."""
        return f"Processed: {data}"

    async with Client(mcp) as client:
        tools_result = await client.list_tools()
        assert len(tools_result) == 1
        assert tools_result[0].name == "sync_tool"
        # execution should be None for non-task tools (default is False, omitted)
        assert tools_result[0].execution is None
