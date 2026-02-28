import importlib
import json
from typing import Any

import pytest
from mcp.types import ImageContent, TextContent

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.experimental.transforms import CodeMode, MontySandboxProvider
from fastmcp.experimental.transforms.code_mode import _ensure_async
from fastmcp.tools.tool import ToolResult


def _unwrap_result(result: ToolResult) -> Any:
    """Extract the logical return value from a ToolResult."""
    if result.structured_content is not None:
        return result.structured_content

    text_blocks = [
        content.text for content in result.content if isinstance(content, TextContent)
    ]
    if not text_blocks:
        return None

    if len(text_blocks) == 1:
        try:
            return json.loads(text_blocks[0])
        except json.JSONDecodeError:
            return text_blocks[0]

    values: list[Any] = []
    for text in text_blocks:
        try:
            values.append(json.loads(text))
        except json.JSONDecodeError:
            values.append(text)
    return values


def _unwrap_search_results(result: ToolResult) -> list[dict[str, Any]]:
    """Extract the list of tool dicts from a search ToolResult.

    The search tool returns ``list[dict]`` which gets wrapped in
    ``{"result": [...]}`` by the structured-output convention.
    """
    data = _unwrap_result(result)
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    if isinstance(data, list):
        return data
    raise AssertionError(f"Unexpected search result shape: {data!r}")


class _UnsafeTestSandboxProvider:
    """UNSAFE: Uses exec() for testing only. Never use in production."""

    async def run(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Any] | None = None,
    ) -> Any:
        namespace: dict[str, Any] = {}
        if inputs:
            namespace.update(inputs)
        if external_functions:
            namespace.update(
                {key: _ensure_async(value) for key, value in external_functions.items()}
            )

        wrapped = "async def __test_main__():\n"
        for line in code.splitlines():
            wrapped += f"    {line}\n"
        if not code.strip():
            wrapped += "    return None\n"

        exec(wrapped, namespace, namespace)
        return await namespace["__test_main__"]()


async def _run_tool(
    server: FastMCP, name: str, arguments: dict[str, Any]
) -> ToolResult:
    return await server.call_tool(name, arguments)


async def test_code_mode_transform_hides_backend_tools_and_supports_defaults() -> None:
    mcp = FastMCP("CodeMode Test")

    @mcp.tool
    def add(x: int, y: int, workspace_id: str) -> str:
        """Add two numbers with workspace context."""
        return f"{workspace_id}:{x + y}"

    @mcp.tool
    def status() -> str:
        """Get current status."""
        return "ok"

    mcp.add_transform(
        CodeMode(
            default_arguments={"workspace_id": "ws-default"},
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    listed_tools = await mcp.list_tools(run_middleware=False)
    assert {tool.name for tool in listed_tools} == {"search", "execute"}

    search_result = await _run_tool(mcp, "search", {"query": "add numbers"})
    names = [t["name"] for t in _unwrap_search_results(search_result)]
    assert "add" in names

    execute_result = await _run_tool(
        mcp,
        "execute",
        {"code": "return await call_tool('add', {'x': 2, 'y': 3})"},
    )
    assert _unwrap_result(execute_result) == {"result": "ws-default:5"}

    status_result = await _run_tool(
        mcp,
        "execute",
        {"code": "return await call_tool('status', {})"},
    )
    assert _unwrap_result(status_result) == {"result": "ok"}


async def test_code_mode_transform_replaces_listed_tools() -> None:
    mcp = FastMCP("CodeMode Transform")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    listed_tools = await mcp.list_tools(run_middleware=False)
    assert {tool.name for tool in listed_tools} == {"search", "execute"}


async def test_code_mode_tool_descriptions_are_configurable() -> None:
    mcp = FastMCP("CodeMode Descriptions")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(
        CodeMode(
            sandbox_provider=_UnsafeTestSandboxProvider(),
            search_tool_name="search_meta",
            execute_tool_name="execute_meta",
            execute_description="Custom execute description",
        )
    )

    listed_tools = await mcp.list_tools(run_middleware=False)
    by_name = {tool.name: tool for tool in listed_tools}

    assert by_name["execute_meta"].description == "Custom execute description"


async def test_code_mode_default_execute_description() -> None:
    mcp = FastMCP("CodeMode Defaults")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    listed_tools = await mcp.list_tools(run_middleware=False)
    by_name = {tool.name: tool for tool in listed_tools}

    execute_description = by_name["execute"].description or ""

    assert "single block" in execute_description
    assert "Use `return` to produce output." in execute_description
    assert (
        "Only `call_tool(tool_name: str, params: dict) -> Any` is available in scope."
        in execute_description
    )


async def test_code_mode_search_returns_matching_tools() -> None:
    mcp = FastMCP("CodeMode Search")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square of a number."""
        return x * x

    @mcp.tool
    def greet(name: str) -> str:
        """Say hello to someone."""
        return f"Hello, {name}!"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "square number"})
    tools = _unwrap_search_results(result)
    assert len(tools) > 0
    assert tools[0]["name"] == "square"


async def test_code_mode_search_results_include_schema() -> None:
    mcp = FastMCP("CodeMode Output Schema")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square of a number."""
        return x * x

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "square"})
    tools = _unwrap_search_results(result)
    assert len(tools) > 0
    tool_dict = tools[0]
    assert "inputSchema" in tool_dict


async def test_code_mode_execute_respects_disabled_tool_visibility() -> None:
    mcp = FastMCP("CodeMode Disabled")

    @mcp.tool
    def secret() -> str:
        return "nope"

    mcp.disable(names={"secret"}, components={"tool"})
    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    with pytest.raises(ToolError, match=r"Unknown tool"):
        await _run_tool(
            mcp,
            "execute",
            {"code": "return await call_tool('secret', {})"},
        )


async def test_code_mode_search_respects_disabled_tool_visibility() -> None:
    mcp = FastMCP("CodeMode Disabled Search")

    @mcp.tool
    def secret() -> str:
        """A secret tool."""
        return "nope"

    mcp.disable(names={"secret"}, components={"tool"})
    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "secret"})
    tools = _unwrap_search_results(result)
    assert tools == []


async def test_code_mode_execute_respects_tool_auth() -> None:
    mcp = FastMCP("CodeMode Auth")

    @mcp.tool(auth=lambda _ctx: False)
    def protected() -> str:
        return "nope"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    with pytest.raises(ToolError, match=r"Unknown tool"):
        await _run_tool(
            mcp,
            "execute",
            {"code": "return await call_tool('protected', {})"},
        )


async def test_code_mode_search_respects_tool_auth() -> None:
    mcp = FastMCP("CodeMode Auth Search")

    @mcp.tool(auth=lambda _ctx: False)
    def protected() -> str:
        """A protected tool."""
        return "nope"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "protected"})
    tools = _unwrap_search_results(result)
    assert tools == []


async def test_code_mode_shadows_colliding_tool_names() -> None:
    """Backend tools with the same name as meta-tools are shadowed, not rejected."""
    mcp = FastMCP("CodeMode Collision")

    @mcp.tool
    def search() -> str:
        return "real search"

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    tools = await mcp.list_tools(run_middleware=False)
    tool_names = {t.name for t in tools}
    assert tool_names == {"search", "execute"}

    result = await _run_tool(
        mcp, "execute", {"code": 'return await call_tool("ping", {})'}
    )
    assert _unwrap_result(result) == {"result": "pong"}


async def test_code_mode_execute_non_text_content_stringified() -> None:
    mcp = FastMCP("CodeMode NonText")

    @mcp.tool
    def image_tool() -> ImageContent:
        return ImageContent(type="image", data="base64data", mimeType="image/png")

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(
        mcp,
        "execute",
        {"code": "return await call_tool('image_tool', {})"},
    )
    unwrapped = _unwrap_result(result)
    assert isinstance(unwrapped, str)
    assert "base64data" in unwrapped


async def test_monty_provider_raises_informative_error_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MontySandboxProvider()
    real_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None):
        if name == "pydantic_monty":
            raise ModuleNotFoundError("No module named 'pydantic_monty'")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    with pytest.raises(ImportError, match=r"fastmcp\[code-mode\]"):
        await provider.run("return 1")


async def test_code_mode_execute_multi_tool_chaining() -> None:
    """Execute block can chain multiple call_tool() calls."""
    mcp = FastMCP("CodeMode Chaining")

    @mcp.tool
    def double(x: int) -> int:
        return x * 2

    @mcp.tool
    def add_one(x: int) -> int:
        return x + 1

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(
        mcp,
        "execute",
        {
            "code": (
                "a = await call_tool('double', {'x': 3})\n"
                "b = await call_tool('add_one', {'x': a['result']})\n"
                "return b"
            )
        },
    )
    assert _unwrap_result(result) == {"result": 7}


async def test_code_mode_execute_default_arguments_overridden_by_explicit() -> None:
    """Explicit params in call_tool() override default_arguments."""
    mcp = FastMCP("CodeMode Override")

    @mcp.tool
    def greet(name: str, greeting: str) -> str:
        return f"{greeting}, {name}!"

    mcp.add_transform(
        CodeMode(
            default_arguments={"greeting": "Hello"},
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(
        mcp,
        "execute",
        {"code": "return await call_tool('greet', {'name': 'World'})"},
    )
    assert _unwrap_result(result) == {"result": "Hello, World!"}

    result = await _run_tool(
        mcp,
        "execute",
        {
            "code": "return await call_tool('greet', {'name': 'World', 'greeting': 'Hi'})"
        },
    )
    assert _unwrap_result(result) == {"result": "Hi, World!"}


async def test_code_mode_get_tool_returns_meta_tools_and_passes_through() -> None:
    """get_tool returns meta-tools by name and passes through backend tools."""
    mcp = FastMCP("CodeMode GetTool")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    search_tool = await mcp.get_tool("search")
    assert search_tool is not None
    assert search_tool.name == "search"

    execute_tool = await mcp.get_tool("execute")
    assert execute_tool is not None
    assert execute_tool.name == "execute"

    ping_tool = await mcp.get_tool("ping")
    assert ping_tool is not None
    assert ping_tool.name == "ping"


async def test_code_mode_sandbox_error_surfaces_as_tool_error() -> None:
    """Runtime errors in sandbox code surface as ToolError."""
    mcp = FastMCP("CodeMode Errors")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    with pytest.raises(ToolError):
        await _run_tool(mcp, "execute", {"code": "raise ValueError('boom')"})


async def test_monty_provider_forwards_limits() -> None:
    """MontySandboxProvider passes limits through to pydantic-monty."""
    provider = MontySandboxProvider(limits={"max_duration_secs": 0.1})

    with pytest.raises(Exception, match="time limit exceeded"):
        await provider.run("x = 0\nfor _ in range(10**9):\n    x += 1")


async def test_monty_provider_no_limits_by_default() -> None:
    """Without limits, a simple script completes normally."""
    provider = MontySandboxProvider()
    result = await provider.run("return 1 + 2")
    assert result == 3


def test_code_mode_rejects_identical_tool_names() -> None:
    """CodeMode raises ValueError when search and execute names collide."""
    with pytest.raises(ValueError, match="must be different"):
        CodeMode(
            search_tool_name="tools",
            execute_tool_name="tools",
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
