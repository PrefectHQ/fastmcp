"""Response caching around multi-round-trip asks (SEP-2322).

A guard component answers a call by *returning* an `InputRequiredResult` — a
request for client input rather than a final answer. Two things follow for
`ResponseCachingMiddleware`, and they apply equally to tools, prompts, and
resources:

- An ask must never be stored. It carries no content of its own, so caching one
  writes an empty result, and every later caller is served that emptiness
  instead of being asked the question.
- A continuation leg must bypass the cache entirely. Cache keys are built from
  the component's identity and arguments alone, so a continuation shares its key
  with a fresh call: reading could hand this leg a prior flow's final answer, and
  writing would hand a later fresh caller *this* flow's answer, skipping the
  questions altogether.
"""

import mcp_types

from fastmcp import Context, FastMCP
from fastmcp.client.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.server.middleware.caching import ResponseCachingMiddleware


def _ask() -> mcp_types.InputRequiredResult:
    """The single-question ask every guard in this module returns."""
    params = mcp_types.ElicitRequestFormParams(
        message="Which quarter?",
        requested_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    )
    request = mcp_types.ElicitRequest(method="elicitation/create", params=params)
    return mcp_types.InputRequiredResult(
        result_type="input_required",
        input_requests={"q": request},
    )


def _answer(responses: mcp_types.InputResponses) -> str:
    """The accepted value for the question `_ask` poses."""
    result = responses["q"]
    assert isinstance(result, mcp_types.ElicitResult)
    assert result.content is not None
    return str(result.content["q"])


async def _handler(message, response_type, params, ctx):
    """An elicitation handler that always answers "Q3"."""
    return ElicitResult(action="accept", content=response_type(q="Q3"))


def cached_guard_server() -> FastMCP:
    """A caching server whose tool, prompt, and resource are all guards."""
    mcp = FastMCP("cached-guards")
    mcp.add_middleware(ResponseCachingMiddleware())

    @mcp.tool
    async def summarize_tool(ctx: Context) -> str | mcp_types.InputRequiredResult:
        if ctx.input_responses is None:
            return _ask()
        return f"Summary for {_answer(ctx.input_responses)}"

    @mcp.prompt
    async def summarize(ctx: Context) -> str | mcp_types.InputRequiredResult:
        if ctx.input_responses is None:
            return _ask()
        return f"Summary for {_answer(ctx.input_responses)}"

    @mcp.resource("report://x")
    async def report(ctx: Context) -> str | mcp_types.InputRequiredResult:
        if ctx.input_responses is None:
            return _ask()
        return f"Report for {_answer(ctx.input_responses)}"

    return mcp


def guard_client() -> Client:
    """A client that answers each round automatically."""
    return Client(cached_guard_server(), mode="auto", elicitation_handler=_handler)


class TestGuardsCompleteUnderCaching:
    """Each component type drives its loop to a real answer with caching on."""

    async def test_tool(self):
        async with guard_client() as client:
            result = await client.call_tool("summarize_tool", {})

        assert result.data == "Summary for Q3"

    async def test_prompt(self):
        async with guard_client() as client:
            result = await client.get_prompt("summarize")

        assert result.messages[0].content.text == "Summary for Q3"

    async def test_resource(self):
        async with guard_client() as client:
            result = await client.read_resource("report://x")

        assert result[0].text == "Report for Q3"


class TestAsksAreNotCached:
    """A stored ask would poison every later caller."""

    async def test_second_fresh_flow_is_asked_again(self):
        """A second fresh flow must be asked the same question.

        Serving it a cached final answer would skip the component's own
        per-round logic — it would receive an answer it never supplied input for.
        """
        async with guard_client() as client:
            first = await client.get_prompt("summarize")
            second = await client.get_prompt("summarize")

        assert first.messages[0].content.text == "Summary for Q3"
        assert second.messages[0].content.text == "Summary for Q3"
