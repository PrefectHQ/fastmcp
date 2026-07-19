import asyncio
import importlib
import importlib.util
import json
from typing import Any

import pytest
from mcp_types import ImageContent, TextContent

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.experimental.transforms.code_mode import (
    _DEFAULT_LIMITS,
    CodeMode,
    GetSchemas,
    GetToolCatalog,
    MontySandboxProvider,
    Search,
    _ensure_async,
)
from fastmcp.server.context import Context
from fastmcp.tools.base import Tool, ToolResult

requires_monty = pytest.mark.skipif(
    importlib.util.find_spec("pydantic_monty") is None,
    reason="pydantic-monty is required for the real Monty sandbox provider",
)

# test_code_mode_monty_bare_call_returns_empty deliberately runs
# `print(call_tool(...))` without awaiting, which orphans a `Provider.get_tool`
# coroutine. CPython reports it as "never awaited" only when the coroutine is
# garbage-collected, which happens asynchronously — often partway through a
# *later* test — so a per-test filter can't reliably catch it. The suite
# promotes that warning (and its unraisable-teardown variant) to an error via
# `filterwarnings` in pyproject.toml, so scope the suppression to the module
# but pin it to that exact coroutine; genuine "never awaited" leaks elsewhere
# still surface as errors.
pytestmark = [
    pytest.mark.filterwarnings(
        "ignore:coroutine 'Provider.get_tool' was never awaited:RuntimeWarning"
    ),
    pytest.mark.filterwarnings(
        "ignore:Exception ignored in.*Provider.get_tool:"
        "pytest.PytestUnraisableExceptionWarning"
    ),
]


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


def _unwrap_string_result(result: ToolResult) -> str:
    """Extract a string result from a ToolResult.

    String results are wrapped in ``{"result": "..."}`` by the
    structured-output convention.
    """
    data = _unwrap_result(result)
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    assert isinstance(data, str)
    return data


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


# ---------------------------------------------------------------------------
# CodeMode core tests
# ---------------------------------------------------------------------------


async def test_code_mode_default_tools() -> None:
    """Default CodeMode exposes search, get_schema, and execute."""
    mcp = FastMCP("CodeMode Default")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    listed_tools = await mcp.list_tools(run_middleware=False)
    assert {tool.name for tool in listed_tools} == {"search", "get_schema", "execute"}


async def test_code_mode_search_returns_lightweight_results() -> None:
    """Default search returns tool names and descriptions, not full schemas."""
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
    text = _unwrap_string_result(result)
    assert "square" in text
    assert "Compute the square" in text
    # Should NOT contain full schema details
    assert "inputSchema" not in text


async def test_code_mode_get_schema_brief() -> None:
    """get_schema with detail=brief returns names and descriptions only."""
    mcp = FastMCP("CodeMode Schema Brief")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square of a number."""
        return x * x

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(
        mcp, "get_schema", {"tools": ["square"], "detail": "brief"}
    )
    text = _unwrap_string_result(result)
    assert "square" in text
    assert "Compute the square" in text
    # brief should NOT include parameter details
    assert "**Parameters**" not in text


async def test_code_mode_get_schema_detailed() -> None:
    """get_schema with detail=detailed returns markdown with parameter info."""
    mcp = FastMCP("CodeMode Schema Detailed")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square of a number."""
        return x * x

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(
        mcp, "get_schema", {"tools": ["square"], "detail": "detailed"}
    )
    text = _unwrap_string_result(result)
    assert "### square" in text
    assert "Compute the square" in text
    assert "**Parameters**" in text
    assert "`x` (integer, required)" in text


async def test_code_mode_get_schema_full() -> None:
    """get_schema with detail=full returns JSON schema."""
    mcp = FastMCP("CodeMode Schema Full")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square of a number."""
        return x * x

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "get_schema", {"tools": ["square"], "detail": "full"})
    text = _unwrap_string_result(result)
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert parsed[0]["name"] == "square"
    assert "inputSchema" in parsed[0]


async def test_code_mode_get_schema_default_is_detailed() -> None:
    """get_schema defaults to detailed (markdown with parameters)."""
    mcp = FastMCP("CodeMode Schema Default")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square of a number."""
        return x * x

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "get_schema", {"tools": ["square"]})
    text = _unwrap_string_result(result)
    assert "### square" in text
    assert "**Parameters**" in text


async def test_code_mode_get_schema_not_found() -> None:
    """get_schema reports tools that don't exist in the catalog."""
    mcp = FastMCP("CodeMode Schema NotFound")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "get_schema", {"tools": ["nonexistent"]})
    text = _unwrap_string_result(result)
    assert "not found" in text.lower()
    assert "nonexistent" in text


async def test_code_mode_get_schema_partial_match() -> None:
    """get_schema returns schemas for found tools and reports missing ones."""
    mcp = FastMCP("CodeMode Schema Partial")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square."""
        return x * x

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "get_schema", {"tools": ["square", "nonexistent"]})
    text = _unwrap_string_result(result)
    assert "### square" in text
    assert "nonexistent" in text


async def test_code_mode_execute_works() -> None:
    """Execute tool can call backend tools through the sandbox."""
    mcp = FastMCP("CodeMode Execute")

    @mcp.tool
    def add(x: int, y: int) -> int:
        return x + y

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(
        mcp, "execute", {"code": "return await call_tool('add', {'x': 2, 'y': 3})"}
    )
    assert _unwrap_result(result) == {"result": 5}


# ---------------------------------------------------------------------------
# Tool naming and configuration
# ---------------------------------------------------------------------------


async def test_code_mode_custom_execute_name() -> None:
    mcp = FastMCP("CodeMode Custom Execute")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(
        CodeMode(
            sandbox_provider=_UnsafeTestSandboxProvider(),
            execute_tool_name="run_code",
        )
    )

    listed = await mcp.list_tools(run_middleware=False)
    names = {t.name for t in listed}
    assert "run_code" in names
    assert "execute" not in names


async def test_code_mode_custom_execute_description() -> None:
    mcp = FastMCP("CodeMode Custom Desc")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(
        CodeMode(
            sandbox_provider=_UnsafeTestSandboxProvider(),
            execute_description="Custom execute description",
        )
    )

    listed = await mcp.list_tools(run_middleware=False)
    by_name = {t.name: t for t in listed}
    assert by_name["execute"].description == "Custom execute description"


async def test_code_mode_default_execute_description() -> None:
    mcp = FastMCP("CodeMode Defaults")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    listed = await mcp.list_tools(run_middleware=False)
    by_name = {t.name: t for t in listed}
    desc = by_name["execute"].description or ""

    assert "single block" in desc
    assert "Use `return` to produce output." in desc
    assert (
        "Only `call_tool(tool_name: str, params: dict) -> Any` is available in scope."
        in desc
    )


# ---------------------------------------------------------------------------
# Discovery tool customization
# ---------------------------------------------------------------------------


async def test_code_mode_no_discovery_tools() -> None:
    """CodeMode with empty discovery_tools exposes only execute."""
    mcp = FastMCP("CodeMode No Discovery")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(
        CodeMode(
            discovery_tools=[],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    listed = await mcp.list_tools(run_middleware=False)
    assert {t.name for t in listed} == {"execute"}


async def test_code_mode_custom_discovery_tool_function() -> None:
    """A plain function can serve as a discovery tool factory."""
    mcp = FastMCP("CodeMode Custom Discovery")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square."""
        return x * x

    def list_all(get_catalog: GetToolCatalog) -> Tool:
        async def list_tools(
            ctx: Context = None,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        ) -> str:
            """List all available tools."""
            tools = await get_catalog(ctx)
            return ", ".join(t.name for t in tools)

        return Tool.from_function(fn=list_tools, name="list_all")

    mcp.add_transform(
        CodeMode(
            discovery_tools=[list_all],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    listed = await mcp.list_tools(run_middleware=False)
    assert {t.name for t in listed} == {"list_all", "execute"}

    result = await _run_tool(mcp, "list_all", {})
    text = _unwrap_string_result(result)
    assert "square" in text


async def test_code_mode_search_detailed() -> None:
    """Search with detail='detailed' returns markdown with parameter info."""
    mcp = FastMCP("CodeMode Search Detailed")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square."""
        return x * x

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "square", "detail": "detailed"})
    text = _unwrap_string_result(result)
    assert "### square" in text
    assert "Compute the square" in text
    assert "**Parameters**" in text
    assert "`x` (integer, required)" in text


async def test_code_mode_search_tool_full_detail() -> None:
    """Search with detail='full' includes JSON schemas."""
    mcp = FastMCP("CodeMode Search Full")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square."""
        return x * x

    mcp.add_transform(
        CodeMode(
            discovery_tools=[Search(default_detail="full")],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "search", {"query": "square"})
    text = _unwrap_string_result(result)
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert parsed[0]["name"] == "square"
    assert "inputSchema" in parsed[0]


async def test_code_mode_custom_search_tool_name() -> None:
    """Search and GetSchemas support custom names."""
    mcp = FastMCP("CodeMode Custom Names")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(
        CodeMode(
            discovery_tools=[
                Search(name="find"),
                GetSchemas(name="describe"),
            ],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    listed = await mcp.list_tools(run_middleware=False)
    assert {t.name for t in listed} == {"find", "describe", "execute"}


def test_code_mode_rejects_discovery_execute_name_collision() -> None:
    """CodeMode raises ValueError when a discovery tool collides with execute."""
    cm = CodeMode(
        discovery_tools=[Search(name="execute")],
        sandbox_provider=_UnsafeTestSandboxProvider(),
    )
    with pytest.raises(ValueError, match="collides"):
        cm._build_discovery_tools()


def test_code_mode_rejects_duplicate_discovery_names() -> None:
    """CodeMode raises ValueError when discovery tools have duplicate names."""
    cm = CodeMode(
        discovery_tools=[Search(name="search"), Search(name="search")],
        sandbox_provider=_UnsafeTestSandboxProvider(),
    )
    with pytest.raises(ValueError, match="unique"):
        cm._build_discovery_tools()


# ---------------------------------------------------------------------------
# Visibility and auth
# ---------------------------------------------------------------------------


async def test_code_mode_execute_respects_disabled_tool_visibility() -> None:
    mcp = FastMCP("CodeMode Disabled")

    @mcp.tool
    def secret() -> str:
        return "nope"

    mcp.disable(names={"secret"}, components={"tool"})
    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    with pytest.raises(ToolError, match=r"Unknown tool"):
        await _run_tool(
            mcp, "execute", {"code": "return await call_tool('secret', {})"}
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
    text = _unwrap_string_result(result)
    assert "secret" not in text or "No tools" in text


async def test_code_mode_execute_sees_mid_run_visibility_changes() -> None:
    """Unlocking a tool mid-execution makes it callable in the same run."""
    mcp = FastMCP("CodeMode Unlock")

    @mcp.tool
    async def unlock(ctx: Context) -> str:
        await ctx.enable_components(names={"secret"}, components={"tool"})
        return "unlocked"

    @mcp.tool
    async def secret() -> str:
        return "secret-ok"

    mcp.disable(names={"secret"}, components={"tool"})
    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    async with Client(mcp) as client:
        result = await client.call_tool(
            "execute",
            {
                "code": "await call_tool('unlock', {})\nreturn await call_tool('secret', {})"
            },
        )
        assert result.data == {"result": "secret-ok"}


async def test_code_mode_execute_respects_tool_auth() -> None:
    mcp = FastMCP("CodeMode Auth")

    @mcp.tool(auth=lambda _ctx: False)
    def protected() -> str:
        return "nope"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    with pytest.raises(ToolError, match=r"Unknown tool"):
        await _run_tool(
            mcp, "execute", {"code": "return await call_tool('protected', {})"}
        )


async def test_code_mode_search_respects_tool_auth() -> None:
    mcp = FastMCP("CodeMode Auth Search")

    @mcp.tool(auth=lambda _ctx: False)
    def protected() -> str:
        """A protected tool."""
        return "nope"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "protected"})
    text = _unwrap_string_result(result)
    assert "protected" not in text or "No tools" in text


async def test_code_mode_shadows_colliding_tool_names() -> None:
    """Backend tools with the same name as meta-tools are shadowed."""
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
    assert "execute" in tool_names

    result = await _run_tool(
        mcp, "execute", {"code": 'return await call_tool("ping", {})'}
    )
    assert _unwrap_result(result) == {"result": "pong"}


# ---------------------------------------------------------------------------
# get_tool pass-through
# ---------------------------------------------------------------------------


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

    schema_tool = await mcp.get_tool("get_schema")
    assert schema_tool is not None
    assert schema_tool.name == "get_schema"

    execute_tool = await mcp.get_tool("execute")
    assert execute_tool is not None
    assert execute_tool.name == "execute"

    ping_tool = await mcp.get_tool("ping")
    assert ping_tool is not None
    assert ping_tool.name == "ping"


# ---------------------------------------------------------------------------
# Execute edge cases
# ---------------------------------------------------------------------------


async def test_code_mode_execute_non_text_content_stringified() -> None:
    mcp = FastMCP("CodeMode NonText")

    @mcp.tool
    def image_tool() -> ImageContent:
        return ImageContent(type="image", data="base64data", mime_type="image/png")

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(
        mcp, "execute", {"code": "return await call_tool('image_tool', {})"}
    )
    unwrapped = _unwrap_result(result)
    assert isinstance(unwrapped, str)
    assert "base64data" in unwrapped


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


async def test_code_mode_sandbox_error_surfaces_as_tool_error() -> None:
    mcp = FastMCP("CodeMode Errors")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeMode(sandbox_provider=_UnsafeTestSandboxProvider()))

    with pytest.raises(ToolError):
        await _run_tool(mcp, "execute", {"code": "raise ValueError('boom')"})


# ---------------------------------------------------------------------------
# Real Monty sandbox end-to-end (issue #4263)
#
# Every execute test above runs through the exec-based
# _UnsafeTestSandboxProvider, so the real MontySandboxProvider + call_tool
# path is otherwise uncovered. These tests drive call_tool through the actual
# pydantic-monty sandbox, the path #4263 reported as broken.
# ---------------------------------------------------------------------------


@requires_monty
@pytest.mark.parametrize("async_tool", [False, True])
async def test_code_mode_monty_execute_call_tool(async_tool: bool) -> None:
    """call_tool resolves and returns through the real Monty sandbox.

    The reported failure ("empty result") could not be reproduced on a
    directly-registered tool: ``return await call_tool(...)`` returns the
    value for both sync and async backend tools.
    """
    mcp = FastMCP("CodeMode Monty Execute")

    if async_tool:

        @mcp.tool
        async def add(x: int, y: int) -> int:
            return x + y
    else:

        @mcp.tool
        def add(x: int, y: int) -> int:
            return x + y

    mcp.add_transform(CodeMode(sandbox_provider=MontySandboxProvider()))

    result = await _run_tool(
        mcp, "execute", {"code": "return await call_tool('add', {'x': 2, 'y': 3})"}
    )
    assert _unwrap_result(result) == {"result": 5}


@requires_monty
async def test_code_mode_monty_execute_string_result() -> None:
    """A string-returning tool round-trips through the real Monty sandbox."""
    mcp = FastMCP("CodeMode Monty String")

    @mcp.tool
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    mcp.add_transform(CodeMode(sandbox_provider=MontySandboxProvider()))

    result = await _run_tool(
        mcp, "execute", {"code": "return await call_tool('greet', {'name': 'World'})"}
    )
    assert _unwrap_string_result(result) == "Hello, World!"


@requires_monty
async def test_code_mode_monty_execute_chaining() -> None:
    """Multiple sequential call_tool() calls chain through the real sandbox."""
    mcp = FastMCP("CodeMode Monty Chaining")

    @mcp.tool
    def add(x: int, y: int) -> int:
        return x + y

    mcp.add_transform(CodeMode(sandbox_provider=MontySandboxProvider()))

    result = await _run_tool(
        mcp,
        "execute",
        {
            "code": (
                "a = await call_tool('add', {'x': 1, 'y': 2})\n"
                "b = await call_tool('add', {'x': a['result'], 'y': 10})\n"
                "return b"
            )
        },
    )
    assert _unwrap_result(result) == {"result": 13}


@requires_monty
async def test_code_mode_monty_bare_call_returns_empty() -> None:
    """Pins the reported #4263 symptom as a usage error, not a sandbox bug.

    The report's code called ``call_tool`` bare — ``print(call_tool(...))``
    without ``await`` or ``return``. ``call_tool`` is async, so a bare call
    hands back an unawaited coroutine, and ``print`` returns ``None``; the
    block therefore returns nothing and ``execute`` yields an empty result.
    The accompanying "coroutine ... was never awaited" RuntimeWarning is a
    cascading effect of that unawaited coroutine being garbage-collected, not
    a separate defect (see the module-level ``filterwarnings`` note for why it
    is suppressed rather than asserted).
    """
    mcp = FastMCP("CodeMode Monty Bare Call")

    @mcp.tool
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    mcp.add_transform(CodeMode(sandbox_provider=MontySandboxProvider()))

    result = await _run_tool(
        mcp, "execute", {"code": "print(call_tool('greet', {'name': 'World'}))"}
    )
    assert result.content == []
    assert result.structured_content is None


# ---------------------------------------------------------------------------
# Sandbox provider tests
# ---------------------------------------------------------------------------


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


async def test_monty_provider_forwards_limits() -> None:
    provider = MontySandboxProvider(limits={"max_duration_secs": 0.1})

    with pytest.raises(Exception, match="time limit exceeded"):
        await provider.run("x = 0\nfor _ in range(10**9):\n    x += 1")


async def test_monty_provider_applies_default_limits() -> None:
    provider = MontySandboxProvider()
    assert provider.limits == _DEFAULT_LIMITS
    # Default limits are generous enough for ordinary code.
    result = await provider.run("return 1 + 2")
    assert result == 3


async def test_monty_provider_explicit_none_disables_limits() -> None:
    provider = MontySandboxProvider(limits=None)
    assert provider.limits is None
    result = await provider.run("return 1 + 2")
    assert result == 3


async def test_monty_provider_explicit_limits_override_defaults() -> None:
    provider = MontySandboxProvider(limits={"max_duration_secs": 0.1})
    assert provider.limits == {"max_duration_secs": 0.1}


async def test_monty_provider_default_limits_are_not_shared_between_instances() -> None:
    """Each default provider must own its limits dict.

    `limits` is a mutable public attribute; if instances shared the
    module-level baseline, mutating one would silently change the defaults
    for every other default provider in the process.
    """
    a = MontySandboxProvider()
    b = MontySandboxProvider()

    assert a.limits is not b.limits
    assert a.limits is not _DEFAULT_LIMITS

    assert a.limits is not None
    a.limits["max_duration_secs"] = 1

    assert b.limits == {"max_duration_secs": 30.0, "max_memory": 100_000_000}
    assert _DEFAULT_LIMITS == {"max_duration_secs": 30.0, "max_memory": 100_000_000}


async def test_code_mode_max_tool_calls_default_is_50() -> None:
    assert CodeMode().max_tool_calls == 50


async def test_code_mode_max_tool_calls_enforced() -> None:
    mcp = FastMCP("CodeMode ToolCap")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(
        CodeMode(sandbox_provider=_UnsafeTestSandboxProvider(), max_tool_calls=3)
    )

    code = "\n".join(
        [
            "results = []",
            "for _ in range(5):",
            "    results.append(await call_tool('ping', {}))",
            "return results",
        ]
    )
    with pytest.raises(ToolError, match=r"Tool call limit exceeded: at most 3"):
        await _run_tool(mcp, "execute", {"code": code})


async def test_code_mode_max_tool_calls_none_is_unlimited() -> None:
    mcp = FastMCP("CodeMode ToolCapNone")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(
        CodeMode(sandbox_provider=_UnsafeTestSandboxProvider(), max_tool_calls=None)
    )

    code = "\n".join(
        [
            "n = 0",
            "for _ in range(60):",
            "    await call_tool('ping', {})",
            "    n += 1",
            "return n",
        ]
    )
    result = await _run_tool(mcp, "execute", {"code": code})
    assert _unwrap_result(result) == 60


async def test_monty_provider_cancels_future_when_task_cancelled() -> None:
    """Cancelling the awaiting task must cancel the underlying sandbox future.

    Otherwise the native Monty thread keeps running to completion after a
    client disconnects or the request times out. A subclass overrides the
    launch seam so the cancellation handling in `run()` is exercised against
    a controllable future rather than a live sandbox thread.
    """
    loop = asyncio.get_running_loop()
    sandbox_future: asyncio.Future[Any] = loop.create_future()

    class _NeverFinishingProvider(MontySandboxProvider):
        def _run_monty(self, monty: Any, *, inputs: Any, external_functions: Any):
            return sandbox_future

    provider = _NeverFinishingProvider()
    task = asyncio.create_task(provider.run("return 1"))

    # Advance the task to `await future` (no suspension point before it).
    for _ in range(3):
        await asyncio.sleep(0)
        if not task.done():
            break

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sandbox_future.cancelled()
