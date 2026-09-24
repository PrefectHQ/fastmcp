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


@pytest.mark.parametrize("mode", MODES)
async def test_completion_does_not_poison_a_response_cache(mode):
    """A completion must not make a caching layer store a listing that a
    generic-hook filter never saw (4.0.7 served hidden prompts this way)."""
    from fastmcp.server.middleware import Middleware
    from fastmcp.server.middleware.caching import ResponseCachingMiddleware

    class HideInternal(Middleware):
        async def on_request(self, context, call_next):
            result = await call_next(context)
            if context.method == "prompts/list":
                return [p for p in result if "internal" not in p.name]
            return result

    mcp = FastMCP(middleware=[ResponseCachingMiddleware(), HideInternal()])

    @mcp.prompt
    def public(x: str) -> str:
        return x

    @mcp.prompt
    def internal_admin(x: str) -> str:
        return x

    @mcp.completion
    def complete(ref, argument, context):
        return ["v"]

    async with Client(mcp, mode=mode) as first, Client(mcp, mode=mode) as second:
        await first.complete(PromptReference(name="public"), {"name": "x", "value": ""})
        listed = [p.name for p in await second.list_prompts()]
    assert listed == ["public"]


@pytest.mark.parametrize("mode", MODES)
async def test_generic_middleware_sees_one_request_per_completion(mode):
    """Rate limits, logging, and metrics must see a completion as one request
    (4.0.6 charged each completion two or three times)."""
    from fastmcp.server.middleware import Middleware

    seen: list[str] = []

    class Recorder(Middleware):
        async def on_request(self, context, call_next):
            seen.append(context.method)
            return await call_next(context)

    mcp = FastMCP(middleware=[Recorder()])

    @mcp.prompt
    def poem(theme: str) -> str:
        return theme

    @mcp.resource("notes://{path}")
    def note(path: str) -> str:
        return path

    @mcp.completion
    def complete(ref, argument, context):
        return ["v"]

    async with Client(mcp, mode=mode) as client:
        seen.clear()
        await client.complete(
            PromptReference(name="poem"), {"name": "theme", "value": ""}
        )
        await client.complete(
            ResourceTemplateReference(uri="notes://{path}"),
            {"name": "path", "value": ""},
        )
    assert seen == ["completion/complete", "completion/complete"]


def _suggesting_server(**kwargs: Any) -> FastMCP:
    mcp = FastMCP("hidden-completion", **kwargs)

    @mcp.prompt
    def poem(theme: str) -> str:
        return f"Write a poem about {theme}"

    @mcp.resource("notes://{path}")
    def note(path: str) -> str:
        return path

    @mcp.completion
    def complete(ref, argument, context):
        return ["private/salary.md"]

    return mcp


HIDDEN_REFS = [
    PromptReference(name="poem"),
    ResourceTemplateReference(uri="notes://{path}"),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("ref", HIDDEN_REFS, ids=["prompt", "template"])
async def test_completion_is_empty_for_refs_hidden_by_auth_middleware(ref, mode):
    from fastmcp.server.middleware import AuthMiddleware

    mcp = _suggesting_server(middleware=[AuthMiddleware(auth=lambda ctx: False)])
    async with Client(mcp, mode=mode) as client:
        result = await client.complete(ref, {"name": "path", "value": ""})
    assert result.values == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("ref", HIDDEN_REFS, ids=["prompt", "template"])
async def test_completion_is_empty_for_disabled_refs(ref, mode):
    mcp = _suggesting_server()
    mcp.disable(names={"poem"})
    mcp.disable(keys={"template:notes://{path}@"})
    async with Client(mcp, mode=mode) as client:
        result = await client.complete(ref, {"name": "path", "value": ""})
    assert result.values == []


async def test_completion_answers_mounted_refs_by_their_namespaced_names():
    parent = FastMCP("parent")
    child = FastMCP("child")

    @child.prompt
    def poem(theme: str) -> str:
        return f"Write a poem about {theme}"

    @child.resource("notes://{path}")
    def note(path: str) -> str:
        return path

    parent.mount(child, namespace="kid")

    @parent.completion
    def complete(ref, argument, context):
        return ["value"]

    async with Client(parent) as client:
        prompts = [p.name for p in await client.list_prompts()]
        templates = [t.uri_template for t in await client.list_resource_templates()]
        for ref in [
            PromptReference(name=prompts[0]),
            ResourceTemplateReference(uri=templates[0]),
        ]:
            result = await client.complete(ref, {"name": "x", "value": ""})
            assert result.values == ["value"]


async def test_completion_answers_a_listed_static_resource_uri():
    mcp = FastMCP("static-completion")

    @mcp.resource("config://app")
    def config() -> str:
        return "{}"

    @mcp.completion
    def complete(ref, argument, context):
        return ["value"]

    async with Client(mcp) as client:
        listed = await client.complete(
            ResourceTemplateReference(uri="config://app"), {"name": "x", "value": ""}
        )
        unknown = await client.complete(
            ResourceTemplateReference(uri="config://other"), {"name": "x", "value": ""}
        )
    assert listed.values == ["value"]
    assert unknown.values == []


@pytest.mark.parametrize("ref", HIDDEN_REFS, ids=["prompt", "template"])
async def test_completion_does_not_consult_list_hooks(ref):
    """Completion resolves its reference directly, so list middleware never runs
    for it, not even a list hook that raises."""
    from fastmcp.server.middleware import Middleware

    class BrokenListing(Middleware):
        async def on_list_prompts(self, context, call_next):
            raise RuntimeError("listing is down")

        async def on_list_resource_templates(self, context, call_next):
            raise RuntimeError("listing is down")

        async def on_list_resources(self, context, call_next):
            raise RuntimeError("listing is down")

    mcp = _suggesting_server(middleware=[BrokenListing()])
    async with Client(mcp) as client:
        result = await client.complete(ref, {"name": "path", "value": ""})
    assert result.values == ["private/salary.md"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("ref", HIDDEN_REFS, ids=["prompt", "template"])
async def test_on_complete_hook_runs_once_per_completion(ref, mode):
    from fastmcp.server.middleware import Middleware

    seen: list[tuple[str, str]] = []

    class Recorder(Middleware):
        async def on_request(self, context, call_next):
            seen.append(("request", context.method))
            return await call_next(context)

        async def on_complete(self, context, call_next):
            seen.append(("complete", context.message.argument.name))
            return await call_next(context)

    mcp = _suggesting_server(middleware=[Recorder()])
    async with Client(mcp, mode=mode) as client:
        seen.clear()
        result = await client.complete(ref, {"name": "path", "value": ""})
    assert result.values == ["private/salary.md"]
    assert seen == [("request", "completion/complete"), ("complete", "path")]


async def test_completion_does_not_run_mounted_server_middleware():
    from fastmcp.server.middleware import Middleware

    child_seen: list[str] = []

    class ChildRecorder(Middleware):
        async def on_message(self, context, call_next):
            child_seen.append(context.method)
            return await call_next(context)

    child = FastMCP("child", middleware=[ChildRecorder()])

    @child.prompt
    def poem(theme: str) -> str:
        return theme

    parent = FastMCP("parent")
    parent.mount(child, namespace="kid")

    @parent.prompt
    def local(theme: str) -> str:
        return theme

    @parent.completion
    def complete(ref, argument, context):
        return ["v"]

    async with Client(parent) as client:
        child_seen.clear()
        own = await client.complete(
            PromptReference(name="local"), {"name": "theme", "value": ""}
        )
        mounted = await client.complete(
            PromptReference(name="kid_poem"), {"name": "theme", "value": ""}
        )
    assert own.values == ["v"]
    assert mounted.values == ["v"]
    assert child_seen == []


@pytest.mark.parametrize("mode", MODES)
async def test_hidden_and_unknown_refs_complete_identically(mode):
    from fastmcp.server.auth import AuthContext
    from fastmcp.server.middleware import AuthMiddleware

    def deny_secret(ctx: AuthContext) -> bool:
        return "secret" not in ctx.component.name

    mcp = FastMCP(middleware=[AuthMiddleware(auth=deny_secret)])

    @mcp.prompt
    def secret_middleware(x: str) -> str:
        return x

    @mcp.prompt(auth=lambda ctx: False)
    def component_auth(x: str) -> str:
        return x

    @mcp.prompt
    def disabled(x: str) -> str:
        return x

    mcp.disable(names={"disabled"})

    @mcp.completion
    def complete(ref, argument, context):
        return ["v"]

    async with Client(mcp, mode=mode) as client:
        results = [
            await client.complete(
                PromptReference(name=name), {"name": "x", "value": ""}
            )
            for name in ["secret_middleware", "component_auth", "disabled", "missing"]
        ]
    assert [r.model_dump() for r in results] == [results[3].model_dump()] * 4
    assert results[3].values == []


@pytest.mark.parametrize("mode", MODES)
async def test_completion_finds_a_query_only_template_by_its_template_string(mode):
    mcp = FastMCP("query-template")

    @mcp.resource("search://items{?q}")
    def search(q: str = "") -> str:
        return q

    child = FastMCP("child")

    @child.resource("find://items{?q}")
    def find(q: str = "") -> str:
        return q

    mcp.mount(child, namespace="kid")

    @mcp.completion
    def complete(ref, argument, context):
        return ["v"]

    async with Client(mcp, mode=mode) as client:
        templates = [t.uri_template for t in await client.list_resource_templates()]
        assert templates == ["search://items{?q}", "find://kid/items{?q}"]
        for uri in templates:
            result = await client.complete(
                ResourceTemplateReference(uri=uri), {"name": "q", "value": ""}
            )
            assert result.values == ["v"]


@pytest.mark.parametrize("disabled", [None, "notes://{id}", "notes://{path}"])
@pytest.mark.parametrize("id_first", [True, False], ids=["id-first", "path-first"])
async def test_completion_resolves_overlapping_templates_by_exact_string(
    disabled, id_first
):
    """Two templates that match each other's strings must each resolve to
    themselves, whatever the registration order."""
    mcp = FastMCP("overlap")

    def by_id(id: str) -> str:
        return id

    def by_path(path: str) -> str:
        return path

    registrations = [("notes://{id}", by_id), ("notes://{path}", by_path)]
    for uri, fn in registrations if id_first else reversed(registrations):
        mcp.resource(uri)(fn)
    if disabled:
        mcp.disable(keys={f"template:{disabled}@"})

    @mcp.completion
    def complete(ref, argument, context):
        return ["v"]

    async with Client(mcp) as client:
        results = {
            uri: (
                await client.complete(
                    ResourceTemplateReference(uri=uri), {"name": "x", "value": ""}
                )
            ).values
            for uri, _ in registrations
        }
    assert results == {
        uri: ([] if uri == disabled else ["v"]) for uri, _ in registrations
    }


@pytest.mark.parametrize("mode", MODES)
async def test_reading_a_template_string_that_does_not_match_itself_is_not_found(mode):
    from fastmcp.exceptions import McpError

    mcp = FastMCP("literal-read")

    @mcp.resource("data://items{?limit}")
    def items(limit: int = 10) -> str:
        return f"limit={limit}"

    async with Client(mcp, mode=mode) as client:
        with pytest.raises(McpError, match="not found"):
            await client.read_resource("data://items{?limit}")
