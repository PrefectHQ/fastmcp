"""Guard-mode multi-round-trip for prompts and resources (SEP-2322).

`InputRequiredResult` is a *result type*, not a `tools/call` feature: any
request can resolve to one. A prompt or resource asks for client input exactly
the way a tool does — return the ask, read `ctx.input_responses` on the round
that follows.

These tests cover the emission side for prompts, concrete resources, and
resource templates, the 2026-07-28 era gate, and the proxy path, where the ask
must be forwarded to the parent rather than answered inside the proxy (a proxy
has no back-channel to the real user). Tool guards live in
``tests/server/test_mrtr_guards.py``.
"""

from __future__ import annotations

import mcp_types
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import ElicitRequest, InputRequiredResult

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult


def _elicit(key: str, message: str, field: str) -> ElicitRequest:
    """A single-field form elicitation request keyed by ``key``."""
    params = mcp_types.ElicitRequestFormParams(
        message=message,
        requested_schema={
            "type": "object",
            "properties": {field: {"type": "string"}},
            "required": [field],
        },
    )
    return ElicitRequest(method="elicitation/create", params=params)


def _ask(
    request: ElicitRequest, key: str, request_state: str | None
) -> InputRequiredResult:
    return InputRequiredResult(
        result_type="input_required",
        input_requests={key: request},
        request_state=request_state,
    )


def _accepted(responses: mcp_types.InputResponses, key: str) -> dict[str, object]:
    """The accepted form content for one answered elicitation."""
    answer = responses[key]
    assert isinstance(answer, mcp_types.ElicitResult)
    assert answer.content is not None
    return dict(answer.content)


def _modern_proxy(backend: FastMCP) -> FastMCP:
    """A proxy whose backend client negotiates the modern era, so the backend
    can emit an `InputRequiredResult` for the proxy to round-trip."""
    from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

    return FastMCPProxy(client_factory=lambda: ProxyClient(backend, mode="auto"))


class TestPromptGuard:
    """`InputRequiredResult` is a result type, not a tools/call feature, so a
    prompt can ask for input the same way a tool does (SEP-2322)."""

    @staticmethod
    def _context_prompt_server() -> FastMCP:
        mcp = FastMCP("prompt-guard")

        @mcp.prompt
        async def summarize(ctx: Context) -> str | InputRequiredResult:
            responses = ctx.input_responses
            if responses is None:
                return _ask(
                    _elicit("context", "What context?", "context"),
                    key="context",
                    request_state=None,
                )
            return f"Summarizing with {_accepted(responses, 'context')['context']}"

        return mcp

    async def test_prompt_emits_input_required(self):
        """The asking round reaches the wire as an InputRequiredResult."""
        async with Client(self._context_prompt_server(), mode="auto") as client:
            result = await client.session.get_prompt(
                "summarize", allow_input_required=True
            )

        assert isinstance(result, InputRequiredResult)
        assert "context" in result.input_requests

    async def test_prompt_completes_with_responses(self):
        """Answering the ask renders the prompt on the next round."""
        mcp = self._context_prompt_server()
        async with Client(mcp, mode="auto") as client:
            ask = await client.session.get_prompt(
                "summarize", allow_input_required=True
            )
            assert isinstance(ask, InputRequiredResult)
            done = await client.session.get_prompt(
                "summarize",
                input_responses={
                    "context": mcp_types.ElicitResult(
                        action="accept", content={"context": "quarterly report"}
                    )
                },
            )

        assert done.messages[0].content.text == ("Summarizing with quarterly report")

    async def test_prompt_guard_rejected_on_handshake_era(self):
        """The result type only exists at 2026-07-28, so an older connection
        gets the era named rather than a generic invalid-result failure."""
        async with Client(self._context_prompt_server(), mode="legacy") as client:
            with pytest.raises(MCPError, match="2026-07-28"):
                await client.session.get_prompt("summarize")


class TestResourceGuard:
    """Resources and templates ask for input the same way tools and prompts do."""

    @staticmethod
    def _resource_server() -> FastMCP:
        mcp = FastMCP("resource-guard")

        @mcp.resource("data://report")
        async def report(ctx: Context) -> str | InputRequiredResult:
            responses = ctx.input_responses
            if responses is None:
                return _ask(
                    _elicit("context", "Which quarter?", "context"),
                    key="context",
                    request_state=None,
                )
            return f"Report for {_accepted(responses, 'context')['context']}"

        @mcp.resource("data://report/{section}")
        async def section_report(
            section: str, ctx: Context
        ) -> str | InputRequiredResult:
            responses = ctx.input_responses
            if responses is None:
                return _ask(
                    _elicit("context", f"Which quarter for {section}?", "context"),
                    key="context",
                    request_state=None,
                )
            quarter = _accepted(responses, "context")["context"]
            return f"{section} for {quarter}"

        return mcp

    async def test_resource_emits_input_required(self):
        async with Client(self._resource_server(), mode="auto") as client:
            result = await client.session.read_resource(
                "data://report", allow_input_required=True
            )

        assert isinstance(result, InputRequiredResult)
        assert "context" in result.input_requests

    async def test_resource_completes_with_responses(self):
        async with Client(self._resource_server(), mode="auto") as client:
            done = await client.session.read_resource(
                "data://report",
                input_responses={
                    "context": mcp_types.ElicitResult(
                        action="accept", content={"context": "Q3"}
                    )
                },
            )

        assert done.contents[0].text == "Report for Q3"

    async def test_resource_template_emits_input_required(self):
        """Templates share the converter, so the ask survives there too."""
        async with Client(self._resource_server(), mode="auto") as client:
            result = await client.session.read_resource(
                "data://report/revenue", allow_input_required=True
            )

        assert isinstance(result, InputRequiredResult)
        assert "context" in result.input_requests

    async def test_resource_guard_rejected_on_handshake_era(self):
        async with Client(self._resource_server(), mode="legacy") as client:
            with pytest.raises(MCPError, match="2026-07-28"):
                await client.session.read_resource("data://report")


class TestProxyForwarding:
    """A proxy forwards a backend guard's ask instead of answering it."""

    async def test_guard_prompt_round_trips_through_proxy(self):
        """A guard prompt behind a proxy surfaces its ask instead of the proxy
        trying to answer it. The proxy has no back-channel to the real user, so
        driving the ask internally fails with "Elicitation not supported"."""
        backend = TestPromptGuard._context_prompt_server()

        async def answer(message, response_type, params, ctx):
            return ElicitResult(
                action="accept", content=response_type(context="quarterly report")
            )

        async with Client(
            _modern_proxy(backend), mode="auto", elicitation_handler=answer
        ) as client:
            result = await client.get_prompt("summarize")

        assert result.messages[0].content.text == "Summarizing with quarterly report"

    async def test_guard_resource_round_trips_through_proxy(self):
        """Concrete resources and templates forward the ask the same way."""
        backend = TestResourceGuard._resource_server()

        async def answer(message, response_type, params, ctx):
            return ElicitResult(action="accept", content=response_type(context="Q3"))

        async with Client(
            _modern_proxy(backend), mode="auto", elicitation_handler=answer
        ) as client:
            direct = await client.read_resource("data://report")
            templated = await client.read_resource("data://report/revenue")

        assert direct[0].text == "Report for Q3"
        assert templated[0].text == "revenue for Q3"
