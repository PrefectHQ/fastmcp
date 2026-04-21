import json
from typing import Any

from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.server.plugins.code_mode import (
    GetTags,
    ListTools,
    Search,
)
from fastmcp.server.plugins.code_mode.sandbox import _ensure_async
from fastmcp.server.plugins.code_mode.transform import CodeModeTransform
from fastmcp.tools.base import ToolResult


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
    """Extract a string result from a ToolResult."""
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
# Tags discovery tool
# ---------------------------------------------------------------------------


async def test_categories_brief_shows_tag_counts() -> None:
    mcp = FastMCP("Tags Brief")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        return x + y

    @mcp.tool(tags={"math"})
    def multiply(x: int, y: int) -> int:
        return x * y

    @mcp.tool(tags={"text"})
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[GetTags()],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "tags", {})
    text = _unwrap_string_result(result)
    assert "math (2 tools)" in text
    assert "text (1 tool)" in text


async def test_categories_full_lists_tools_per_tag() -> None:
    mcp = FastMCP("Tags Full")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    @mcp.tool(tags={"text"})
    def greet(name: str) -> str:
        """Say hello."""
        return f"Hello, {name}!"

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[GetTags(default_detail="full")],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "tags", {})
    text = _unwrap_string_result(result)
    assert "### math" in text
    assert "- add: Add two numbers." in text
    assert "### text" in text
    assert "- greet: Say hello." in text


async def test_categories_includes_untagged() -> None:
    mcp = FastMCP("Tags Untagged")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        return x + y

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[GetTags()],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "tags", {})
    text = _unwrap_string_result(result)
    assert "math" in text
    assert "untagged (1 tool)" in text


async def test_categories_tool_in_multiple_tags() -> None:
    mcp = FastMCP("Tags Multi-tag")

    @mcp.tool(tags={"math", "core"})
    def add(x: int, y: int) -> int:
        return x + y

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[GetTags(default_detail="full")],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "tags", {})
    text = _unwrap_string_result(result)
    assert "### core" in text
    assert "### math" in text
    # Tool appears under both tags
    assert text.count("- add") == 2


async def test_categories_detail_override_per_call() -> None:
    """LLM can override default_detail on a per-call basis."""
    mcp = FastMCP("Tags Override")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add numbers."""
        return x + y

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[GetTags()],  # default_detail="brief"
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    # Override to full
    result = await _run_tool(mcp, "tags", {"detail": "full"})
    text = _unwrap_string_result(result)
    assert "### math" in text
    assert "- add: Add numbers." in text


async def test_get_tags_empty_catalog() -> None:
    """GetTags with no tools returns 'No tools available.'."""
    mcp = FastMCP("CodeMode Empty Tags")

    mcp.disable(names={"ping"}, components={"tool"})
    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[GetTags()],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "tags", {})
    text = _unwrap_string_result(result)
    assert "No tools available" in text


# ---------------------------------------------------------------------------
# Search with tags filtering
# ---------------------------------------------------------------------------


async def test_search_with_tags_filter() -> None:
    mcp = FastMCP("Search Tags")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    @mcp.tool(tags={"text"})
    def greet(name: str) -> str:
        """Say hello."""
        return f"Hello, {name}!"

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "add hello", "tags": ["math"]})
    text = _unwrap_string_result(result)
    assert "add" in text
    assert "greet" not in text


async def test_search_with_tags_filter_no_matches() -> None:
    mcp = FastMCP("Search Tags Empty")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "add", "tags": ["nonexistent"]})
    text = _unwrap_string_result(result)
    assert "add" not in text or "No tools" in text


async def test_search_without_tags_returns_all() -> None:
    """Search without tags parameter searches the full catalog."""
    mcp = FastMCP("Search No Tags")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    @mcp.tool(tags={"text"})
    def greet(name: str) -> str:
        """Say hello."""
        return f"Hello, {name}!"

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "add hello"})
    text = _unwrap_string_result(result)
    assert "add" in text
    assert "greet" in text


async def test_search_with_untagged_filter() -> None:
    """Search with tags=["untagged"] matches tools that have no tags."""
    mcp = FastMCP("Search Untagged")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    @mcp.tool
    def ping() -> str:
        """Ping."""
        return "pong"

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "ping add", "tags": ["untagged"]})
    text = _unwrap_string_result(result)
    assert "ping" in text
    assert "add" not in text


async def test_search_default_detail_detailed_skips_get_schema() -> None:
    """Two-stage pattern: Search(default_detail='detailed') returns schemas inline."""
    mcp = FastMCP("CodeMode Two-Stage")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square."""
        return x * x

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[Search(default_detail="detailed")],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "search", {"query": "square"})
    text = _unwrap_string_result(result)
    assert "### square" in text
    assert "**Parameters**" in text
    assert "`x` (integer, required)" in text


async def test_search_full_detail_empty_results_returns_json() -> None:
    """Search with detail=full and no matches returns valid JSON, not plain text."""
    mcp = FastMCP("CodeMode Empty Full")

    @mcp.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        return x + y

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(
        mcp,
        "search",
        {"query": "nonexistent", "tags": ["nonexistent"], "detail": "full"},
    )
    text = _unwrap_string_result(result)
    parsed = json.loads(text)
    assert parsed == []


async def test_get_schema_empty_tools_list() -> None:
    """get_schema with an empty tools list returns no-match message."""
    mcp = FastMCP("CodeMode Empty Schema")

    @mcp.tool
    def ping() -> str:
        return "pong"

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "get_schema", {"tools": []})
    text = _unwrap_string_result(result)
    assert "No tools matched" in text


async def test_get_schema_full_partial_match_returns_valid_json() -> None:
    """get_schema with detail=full and missing tools returns valid JSON."""
    mcp = FastMCP("Schema Full Partial")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square."""
        return x * x

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(
        mcp, "get_schema", {"tools": ["square", "nonexistent"], "detail": "full"}
    )
    text = _unwrap_string_result(result)
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert parsed[0]["name"] == "square"
    assert parsed[-1] == {"not_found": ["nonexistent"]}


# ---------------------------------------------------------------------------
# Search catalog size annotation
# ---------------------------------------------------------------------------


async def test_search_shows_catalog_size_when_results_are_subset() -> None:
    """Search annotates results with 'N of M tools' when not all tools match."""
    mcp = FastMCP("Search Annotation")

    @mcp.tool
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    @mcp.tool
    def multiply(x: int, y: int) -> int:
        """Multiply two numbers."""
        return x * y

    @mcp.tool
    def greet(name: str) -> str:
        """Say hello."""
        return f"Hello, {name}!"

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "add numbers"})
    text = _unwrap_string_result(result)
    # Should show partial result count out of total catalog
    assert "of 3 tools:" in text


async def test_search_omits_annotation_when_all_tools_returned() -> None:
    """Search does not annotate when results include every tool."""
    mcp = FastMCP("Search All Match")

    @mcp.tool
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "add numbers"})
    text = _unwrap_string_result(result)
    assert "of" not in text or "tools:" not in text


# ---------------------------------------------------------------------------
# Search limit
# ---------------------------------------------------------------------------


async def test_search_limit_caps_results() -> None:
    """Search with limit returns at most that many results."""
    mcp = FastMCP("Search Limit")

    @mcp.tool
    def add(x: int, y: int) -> int:
        """Add numbers."""
        return x + y

    @mcp.tool
    def subtract(x: int, y: int) -> int:
        """Subtract numbers."""
        return x - y

    @mcp.tool
    def multiply(x: int, y: int) -> int:
        """Multiply numbers."""
        return x * y

    mcp.add_transform(CodeModeTransform(sandbox_provider=_UnsafeTestSandboxProvider()))

    result = await _run_tool(mcp, "search", {"query": "numbers", "limit": 1})
    text = _unwrap_string_result(result)
    assert "1 of 3 tools:" in text
    # Only one tool line (starts with "- ")
    tool_lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(tool_lines) == 1


async def test_search_default_limit_from_constructor() -> None:
    """Search(default_limit=N) caps results by default."""
    mcp = FastMCP("Search Default Limit")

    @mcp.tool
    def a() -> str:
        """Tool A."""
        return "a"

    @mcp.tool
    def b() -> str:
        """Tool B."""
        return "b"

    @mcp.tool
    def c() -> str:
        """Tool C."""
        return "c"

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[Search(default_limit=2)],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "search", {"query": "tool"})
    text = _unwrap_string_result(result)
    assert "2 of 3 tools:" in text


# ---------------------------------------------------------------------------
# ListTools discovery tool
# ---------------------------------------------------------------------------


async def test_list_tools_brief() -> None:
    """ListTools at brief detail lists all tool names and descriptions."""
    mcp = FastMCP("ListTools Brief")

    @mcp.tool
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    @mcp.tool
    def multiply(x: int, y: int) -> int:
        """Multiply two numbers."""
        return x * y

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[ListTools()],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "list_tools", {})
    text = _unwrap_string_result(result)
    assert "add" in text
    assert "multiply" in text
    assert "Add two numbers" in text
    # brief should not include parameter details
    assert "**Parameters**" not in text


async def test_list_tools_detailed() -> None:
    """ListTools at detailed shows parameter schemas."""
    mcp = FastMCP("ListTools Detailed")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square."""
        return x * x

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[ListTools(default_detail="detailed")],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "list_tools", {})
    text = _unwrap_string_result(result)
    assert "### square" in text
    assert "**Parameters**" in text
    assert "`x` (integer, required)" in text


async def test_list_tools_full_returns_json() -> None:
    """ListTools at full returns valid JSON with schemas."""
    mcp = FastMCP("ListTools Full")

    @mcp.tool
    def ping() -> str:
        """Ping."""
        return "pong"

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[ListTools()],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "list_tools", {"detail": "full"})
    text = _unwrap_string_result(result)
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert parsed[0]["name"] == "ping"


async def test_list_tools_empty_catalog() -> None:
    """ListTools with no tools returns no-match message."""
    mcp = FastMCP("ListTools Empty")

    mcp.add_transform(
        CodeModeTransform(
            discovery_tools=[ListTools()],
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
    )

    result = await _run_tool(mcp, "list_tools", {})
    text = _unwrap_string_result(result)
    assert "No tools matched" in text
