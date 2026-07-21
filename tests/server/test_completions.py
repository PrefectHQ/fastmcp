"""Server-side argument completion (`completion/complete`).

A FastMCP server answers completion requests through a single handler
registered with `@mcp.completion`. These tests cover both reference kinds
(prompt arguments and resource-template parameters), the capability
declaration, graceful handling of unrecognized references, and parity across
the handshake (`mode="legacy"`) and modern (`mode="auto"`) protocol eras.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from mcp_types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
)

from fastmcp import Client, FastMCP
from fastmcp.server.completions import normalize_completion

# Both protocol eras the connection may negotiate.
MODES = ["legacy", "auto"]


@pytest.fixture
def completion_server() -> FastMCP:
    """A server that completes a prompt argument and a template parameter."""
    mcp = FastMCP("completion-server")

    @mcp.prompt
    def poem(theme: str) -> str:
        return f"Write a poem about {theme}"

    @mcp.resource("data://item/{item_id}")
    def item(item_id: str) -> str:
        return f"item-{item_id}"

    @mcp.completion
    def complete(ref, argument, context):
        if isinstance(ref, PromptReference) and ref.name == "poem":
            if argument.name == "theme":
                options = ["nature", "love", "adventure"]
                return [o for o in options if o.startswith(argument.value)]
        if isinstance(ref, ResourceTemplateReference):
            if ref.uri == "data://item/{item_id}" and argument.name == "item_id":
                return ["1", "2", "3"]
        return None

    return mcp


@pytest.mark.parametrize("mode", MODES)
async def test_prompt_argument_completion_returns_candidates(completion_server, mode):
    async with Client(completion_server, mode=mode) as client:
        result = await client.complete(
            PromptReference(name="poem"),
            {"name": "theme", "value": "n"},
        )
    assert result.values == ["nature"]


@pytest.mark.parametrize("mode", MODES)
async def test_resource_template_completion_returns_candidates(completion_server, mode):
    ref = ResourceTemplateReference(uri="data://item/{item_id}")
    async with Client(completion_server, mode=mode) as client:
        result = await client.complete(ref, {"name": "item_id", "value": ""})
    assert result.values == ["1", "2", "3"]


@pytest.mark.parametrize("mode", MODES)
async def test_capability_declared_when_handler_registered(completion_server, mode):
    async with Client(completion_server, mode=mode) as client:
        capabilities = client.server_capabilities
    assert capabilities is not None
    assert capabilities.completions is not None


@pytest.mark.parametrize("mode", MODES)
async def test_capability_absent_without_handler(mode):
    mcp = FastMCP("no-completion")

    @mcp.prompt
    def poem(theme: str) -> str:
        return f"Write a poem about {theme}"

    async with Client(mcp, mode=mode) as client:
        capabilities = client.server_capabilities
    assert capabilities is not None
    assert capabilities.completions is None


@pytest.mark.parametrize("mode", MODES)
async def test_unregistered_ref_returns_empty_completion(completion_server, mode):
    async with Client(completion_server, mode=mode) as client:
        result = await client.complete(
            PromptReference(name="does-not-exist"),
            {"name": "theme", "value": "n"},
        )
    assert result.values == []


@pytest.mark.parametrize("mode", MODES)
async def test_unregistered_argument_returns_empty_completion(completion_server, mode):
    async with Client(completion_server, mode=mode) as client:
        result = await client.complete(
            PromptReference(name="poem"),
            {"name": "unknown_argument", "value": "x"},
        )
    assert result.values == []


@pytest.mark.parametrize("mode", MODES)
async def test_completion_context_reaches_handler(mode):
    """The already-supplied argument values arrive as the handler's context."""
    mcp = FastMCP("context-server")

    @mcp.prompt
    def compose(owner: str, repo: str) -> str:
        return f"{owner}/{repo}"

    seen: dict[str, str] = {}

    @mcp.completion
    def complete(ref, argument, context):
        if context is not None and context.arguments:
            seen.update(context.arguments)
        return ["fastmcp"]

    async with Client(mcp, mode=mode) as client:
        result = await client.complete(
            PromptReference(name="compose"),
            {"name": "repo", "value": "fast"},
            context_arguments={"owner": "prefecthq"},
        )
    assert result.values == ["fastmcp"]
    assert seen == {"owner": "prefecthq"}


@pytest.mark.parametrize("mode", MODES)
async def test_completion_object_passes_through_pagination_hints(mode):
    """Returning a Completion preserves its total / has_more hints."""
    mcp = FastMCP("hints-server")

    @mcp.prompt
    def poem(theme: str) -> str:
        return f"Write a poem about {theme}"

    @mcp.completion
    def complete(ref, argument, context):
        return Completion(values=["nature"], total=42, has_more=True)

    async with Client(mcp, mode=mode) as client:
        result = await client.complete(
            PromptReference(name="poem"),
            {"name": "theme", "value": "n"},
        )
    assert result.values == ["nature"]
    assert result.total == 42
    assert result.has_more is True


async def test_async_completion_handler_is_awaited():
    mcp = FastMCP("async-server")

    @mcp.prompt
    def poem(theme: str) -> str:
        return f"Write a poem about {theme}"

    @mcp.completion
    async def complete(ref, argument, context):
        return ["async-value"]

    async with Client(mcp) as client:
        result = await client.complete(
            PromptReference(name="poem"),
            {"name": "theme", "value": ""},
        )
    assert result.values == ["async-value"]


async def test_sync_completion_handler_runs_off_event_loop_thread():
    """A sync handler is offloaded to a threadpool so blocking work in it can't
    stall the event loop, matching how sync tools/prompts/resources run."""
    mcp = FastMCP("threadpool-server")

    @mcp.prompt
    def poem(theme: str) -> str:
        return f"Write a poem about {theme}"

    handler_thread: dict[str, int] = {}

    @mcp.completion
    def complete(ref, argument, context):
        handler_thread["ident"] = threading.get_ident()
        return ["value"]

    main_thread = threading.get_ident()
    async with Client(mcp) as client:
        result = await client.complete(
            PromptReference(name="poem"),
            {"name": "theme", "value": ""},
        )
    assert result.values == ["value"]
    assert handler_thread["ident"] != main_thread


def test_completion_decorator_registers_handler():
    """`@mcp.completion` (bare) registers the handler and the wire capability."""
    mcp = FastMCP("decorator-server")

    @mcp.completion
    def complete(ref, argument, context):
        return None

    assert mcp._completion_handler is complete
    assert "completion/complete" in mcp._mcp_server._request_handlers


def test_completion_decorator_called_form_registers_handler():
    """`@mcp.completion()` (called) registers the handler too."""
    mcp = FastMCP("decorator-server")

    @mcp.completion()
    def complete(ref, argument, context):
        return None

    assert mcp._completion_handler is complete
    assert "completion/complete" in mcp._mcp_server._request_handlers


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, []),
        ([], []),
        (["a", "b"], ["a", "b"]),
        (("a", "b"), ["a", "b"]),
    ],
)
def test_normalize_completion_coerces_values(value, expected):
    assert normalize_completion(value).values == expected


def test_normalize_completion_passes_completion_through():
    completion = Completion(values=["x"], total=1)
    assert normalize_completion(completion) is completion


def test_normalize_completion_truncates_oversized_list_to_100():
    values = [str(i) for i in range(150)]
    completion = normalize_completion(values)
    assert len(completion.values) == 100
    assert completion.total == 150
    assert completion.has_more is True


def test_normalize_completion_truncates_oversized_completion_and_keeps_total():
    completion = normalize_completion(
        Completion(values=[str(i) for i in range(150)], total=500)
    )
    assert len(completion.values) == 100
    assert completion.total == 500
    assert completion.has_more is True


def test_normalize_completion_rejects_bare_string():
    # A bare str is excluded from the handler return type, so this passes it
    # through an Any-typed value to exercise the runtime guard for callers who
    # bypass type checking.
    bad: Any = "oops"
    with pytest.raises(TypeError, match="return a list of strings"):
        normalize_completion(bad)


def test_completion_argument_and_context_types_importable():
    """The completion authoring types are importable from mcp_types."""
    argument = CompletionArgument(name="theme", value="n")
    context = CompletionContext(arguments={"owner": "prefecthq"})
    assert argument.name == "theme"
    assert context.arguments == {"owner": "prefecthq"}
