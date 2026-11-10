"""Tests for CallArgument and Depends bindings through FastMCP's resolution."""

import pytest
from mcp_types import TextContent

from fastmcp import FastMCP
from fastmcp.dependencies import CallArgument, CycleError, Depends
from fastmcp.server.dependencies import resolve_dependencies


@pytest.fixture
def mcp():
    """Create a FastMCP server for testing."""
    return FastMCP("test-server")


async def test_bare_call_argument_reads_tool_parameter(mcp: FastMCP):
    """A bare CallArgument takes the value of the same-named tool parameter."""

    def get_greeting(name: str = CallArgument()) -> str:
        return f"Hello, {name}!"

    @mcp.tool()
    async def greet(name: str, greeting: str = Depends(get_greeting)) -> str:
        return greeting

    result = await mcp.call_tool("greet", {"name": "Alice"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "Hello, Alice!"


async def test_named_call_argument_reads_tool_parameter(mcp: FastMCP):
    """CallArgument("name") reads a tool parameter with a different name."""

    def get_greeting(who: str = CallArgument("name")) -> str:
        return f"Hello, {who}!"

    @mcp.tool()
    async def greet(name: str, greeting: str = Depends(get_greeting)) -> str:
        return greeting

    result = await mcp.call_tool("greet", {"name": "Bob"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "Hello, Bob!"


async def test_call_argument_in_binding(mcp: FastMCP):
    """A CallArgument binding wires a tool parameter to a factory parameter."""

    def get_account(user_id: str) -> dict[str, str]:
        return {"id": user_id, "plan": "pro"}

    @mcp.tool()
    async def show_account(
        owner: str,
        account: dict[str, str] = Depends(get_account, user_id=CallArgument("owner")),
    ) -> str:
        return f"{account['id']}:{account['plan']}"

    result = await mcp.call_tool("show_account", {"owner": "alice"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "alice:pro"


async def test_plain_value_binding(mcp: FastMCP):
    """A binding that is not a Dependency passes through to the factory as-is."""

    def get_url(scheme: str) -> str:
        return f"{scheme}://example.com"

    @mcp.tool()
    async def fetch(path: str, url: str = Depends(get_url, scheme="https")) -> str:
        return f"{url}/{path}"

    result = await mcp.call_tool("fetch", {"path": "docs"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "https://example.com/docs"


async def test_binding_replaces_factory_depends_default(mcp: FastMCP):
    """A binding replaces the factory's own Depends default, which never runs."""

    default_calls = 0

    def get_default_region() -> str:
        nonlocal default_calls
        default_calls += 1
        return "us-east-1"

    def get_bucket(region: str = Depends(get_default_region)) -> str:
        return f"bucket-{region}"

    @mcp.tool()
    async def store(
        data: str, bucket: str = Depends(get_bucket, region="eu-west-1")
    ) -> str:
        return bucket

    result = await mcp.call_tool("store", {"data": "payload"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "bucket-eu-west-1"
    assert default_calls == 0


async def test_optional_call_argument_yields_none(mcp: FastMCP):
    """CallArgument(optional=True) yields None for a name the tool lacks."""

    def get_note(tenant: str | None = CallArgument("tenant", optional=True)) -> str:
        return f"tenant={tenant}"

    @mcp.tool()
    async def report(topic: str, note: str = Depends(get_note)) -> str:
        return note

    result = await mcp.call_tool("report", {"topic": "sales"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "tenant=None"


async def test_sibling_dependency_resolves_once(mcp: FastMCP):
    """A CallArgument reference to a dependency-backed sibling shares one value."""

    session_calls = 0

    def get_session() -> str:
        nonlocal session_calls
        session_calls += 1
        return "session-1"

    def audit(session: str = CallArgument()) -> str:
        return f"audit:{session}"

    @mcp.tool()
    async def act(
        step: str,
        session: str = Depends(get_session),
        log: str = Depends(audit),
    ) -> str:
        return f"{log}|{session}"

    result = await mcp.call_tool("act", {"step": "one"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "audit:session-1|session-1"
    assert session_calls == 1


async def test_call_argument_cycle_raises_cycle_error():
    """CallArgument references that form a cycle raise CycleError with the path."""

    def get_a(b: str = CallArgument()) -> str:
        return b

    def get_b(a: str = CallArgument()) -> str:
        return a

    async def entangled(a: str = Depends(get_a), b: str = Depends(get_b)) -> str:
        return f"{a}{b}"

    with pytest.raises(CycleError, match="a -> b -> a"):
        async with resolve_dependencies(entangled, {}):
            pass


async def test_colliding_argument_never_reaches_call_argument(mcp: FastMCP):
    """A caller-supplied value for a dependency parameter name is stripped.

    A CallArgument that references the dependency parameter resolves the
    dependency itself, never the caller's value.
    """

    def get_role() -> str:
        return "user"

    def describe(role: str = CallArgument()) -> str:
        return f"role={role}"

    @mcp.prompt()
    async def status(
        topic: str,
        role: str = Depends(get_role),
        summary: str = Depends(describe),
    ) -> str:
        return f"{topic}: {summary}"

    result = await mcp.render_prompt("status", {"topic": "audit", "role": "admin"})
    content = result.messages[0].content
    assert isinstance(content, TextContent)
    assert "role=user" in content.text
    assert "admin" not in content.text
