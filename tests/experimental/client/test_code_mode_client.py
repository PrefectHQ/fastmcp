import importlib.util

import pytest

from fastmcp import Client, ClientGroup, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.experimental.client.code_mode import CodeModeClient
from fastmcp.experimental.transforms.code_mode import GetTags, ListTools, Search

requires_monty = pytest.mark.skipif(
    importlib.util.find_spec("pydantic_monty") is None,
    reason="pydantic-monty is required for the real Monty sandbox provider",
)


@pytest.fixture
def math_server() -> FastMCP:
    mcp = FastMCP("Math")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    @mcp.tool(tags={"math"})
    def multiply(x: int, y: int) -> int:
        """Multiply two numbers."""
        return x * y

    @mcp.tool
    def boom() -> None:
        """Always fails."""
        raise ToolError("deliberate tool failure")

    return mcp


@pytest.fixture
def text_server() -> FastMCP:
    mcp = FastMCP("Text")

    @mcp.tool
    def shout(text: str) -> str:
        """Uppercase text."""
        return text.upper()

    return mcp


async def test_list_tools_returns_only_meta_tools(math_server: FastMCP) -> None:
    async with CodeModeClient(Client(math_server)) as code_mode:
        tools = await code_mode.list_tools()

    assert [tool.name for tool in tools] == ["search", "get_schema", "execute"]


async def test_search_and_get_schema_read_the_remote_catalog(
    math_server: FastMCP,
) -> None:
    async with CodeModeClient(Client(math_server)) as code_mode:
        found = await code_mode.call_tool("search", {"query": "multiply numbers"})
        schema = await code_mode.call_tool("get_schema", {"tools": ["add"]})

    assert "- multiply: Multiply two numbers." in found.data
    assert "### add" in schema.data
    assert "`x` (integer, required)" in schema.data


async def test_discovery_sees_tags_from_remote_meta(math_server: FastMCP) -> None:
    code_mode = CodeModeClient(Client(math_server), discovery_tools=[GetTags()])
    async with code_mode:
        result = await code_mode.call_tool("tags", {})

    assert "- math (2 tools)" in result.data
    assert "- untagged (1 tool)" in result.data


async def test_discovery_reflects_tools_added_after_connect(
    math_server: FastMCP,
) -> None:
    code_mode = CodeModeClient(Client(math_server), discovery_tools=[ListTools()])
    async with code_mode:
        math_server.tool(lambda: "late", name="late_tool")
        result = await code_mode.call_tool("list_tools", {})

    assert "late_tool" in result.data


@requires_monty
async def test_execute_chains_remote_calls(math_server: FastMCP) -> None:
    code = (
        "a = await call_tool('add', {'x': 3, 'y': 4})\n"
        "return await call_tool('multiply', {'x': a['result'], 'y': 2})"
    )
    async with CodeModeClient(Client(math_server)) as code_mode:
        result = await code_mode.call_tool("execute", {"code": code})

    assert result.data == {"result": 14}


@requires_monty
async def test_execute_chains_tools_across_a_client_group(
    math_server: FastMCP, text_server: FastMCP
) -> None:
    group = ClientGroup({"math": Client(math_server), "text": Client(text_server)})
    code = (
        "total = await call_tool('math_add', {'x': 1, 'y': 2})\n"
        "return await call_tool('text_shout', {'text': f\"sum={total['result']}\"})"
    )
    async with CodeModeClient(group) as code_mode:
        result = await code_mode.call_tool("execute", {"code": code})

    assert result.data == {"result": "SUM=3"}


@requires_monty
@pytest.mark.parametrize(
    ("target", "failing_call", "expected_message"),
    [
        (
            "client",
            "await call_tool('boom', {})",
            "call_tool('boom') failed: deliberate tool failure",
        ),
        ("group", "await call_tool('math_nope', {})", "Unknown tool: math_nope"),
    ],
    ids=["tool-error", "group-unknown-tool"],
)
async def test_call_tool_errors_are_catchable_in_the_sandbox(
    math_server: FastMCP, target: str, failing_call: str, expected_message: str
) -> None:
    wrapped = (
        Client(math_server)
        if target == "client"
        else ClientGroup({"math": Client(math_server)})
    )
    add = "add" if target == "client" else "math_add"
    code = (
        f"total = (await call_tool('{add}', {{'x': 2, 'y': 3}}))['result']\n"
        "caught = None\n"
        "try:\n"
        f"    {failing_call}\n"
        "except Exception as exc:\n"
        "    caught = str(exc)\n"
        "return {'caught': caught, 'total': total}"
    )
    async with CodeModeClient(wrapped) as code_mode:
        result = await code_mode.call_tool("execute", {"code": code})

    assert result.data == {"caught": expected_message, "total": 5}


@requires_monty
async def test_validation_errors_list_valid_parameters(math_server: FastMCP) -> None:
    code = "return await call_tool('add', {'x': 1, 'z': 2})"
    async with CodeModeClient(Client(math_server)) as code_mode:
        with pytest.raises(ToolError) as exc_info:
            await code_mode.call_tool("execute", {"code": code})

    message = str(exc_info.value)
    assert "call_tool('add') failed" in message
    assert "Valid parameters for add (* = required): x*, y*" in message


@requires_monty
async def test_uncaught_failure_is_an_error_result(math_server: FastMCP) -> None:
    code = "return await call_tool('boom', {})"
    async with CodeModeClient(Client(math_server)) as code_mode:
        with pytest.raises(ToolError, match="deliberate tool failure"):
            await code_mode.call_tool("execute", {"code": code})
        result = await code_mode.call_tool(
            "execute", {"code": code}, raise_on_error=False
        )

    assert result.is_error
    assert result.data is None


@requires_monty
async def test_max_tool_calls_is_enforced(math_server: FastMCP) -> None:
    code = (
        "for _ in range(3):\n"
        "    await call_tool('add', {'x': 1, 'y': 1})\n"
        "return 'done'"
    )
    code_mode = CodeModeClient(Client(math_server), max_tool_calls=2)
    async with code_mode:
        with pytest.raises(ToolError, match="Tool call limit exceeded: at most 2"):
            await code_mode.call_tool("execute", {"code": code})


async def test_unknown_meta_tool_is_an_error(math_server: FastMCP) -> None:
    async with CodeModeClient(Client(math_server)) as code_mode:
        with pytest.raises(ToolError, match="Unknown tool: 'add'"):
            await code_mode.call_tool("add", {"x": 1, "y": 2})
        raw = await code_mode.call_tool_mcp("add", {"x": 1, "y": 2})

    assert raw.is_error


async def test_invalid_meta_tool_arguments_are_an_error_result(
    math_server: FastMCP,
) -> None:
    async with CodeModeClient(Client(math_server)) as code_mode:
        raw = await code_mode.call_tool_mcp("get_schema", {"tools": "add", "x": 1})

    assert raw.is_error


async def test_custom_execute_name_and_description(math_server: FastMCP) -> None:
    code_mode = CodeModeClient(
        Client(math_server),
        discovery_tools=[],
        execute_tool_name="run_python",
        execute_description="Run it.",
    )
    tools = await code_mode.list_tools()

    assert [(tool.name, tool.description) for tool in tools] == [
        ("run_python", "Run it.")
    ]


def test_discovery_name_colliding_with_execute_is_rejected(
    math_server: FastMCP,
) -> None:
    with pytest.raises(ValueError, match="collides with execute_tool_name"):
        CodeModeClient(Client(math_server), discovery_tools=[Search(name="execute")])


async def test_context_manager_connects_the_wrapped_client(
    math_server: FastMCP,
) -> None:
    client = Client(math_server)
    async with CodeModeClient(client) as code_mode:
        assert code_mode.client is client
        assert client.is_connected()

    assert not client.is_connected()
