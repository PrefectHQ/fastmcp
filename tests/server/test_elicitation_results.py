"""Refused elicitations must not pass a truthiness guard (#4929)."""

from mcp.server.elicitation import CancelledElicitation as SDKCancelledElicitation
from mcp.server.elicitation import DeclinedElicitation as SDKDeclinedElicitation

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)


def test_refused_results_are_falsy():
    assert not DeclinedElicitation()
    assert not CancelledElicitation()


def test_accepted_result_is_truthy_even_for_a_negative_answer():
    assert AcceptedElicitation[bool](data=False)
    assert AcceptedElicitation[str](data="")


def test_refused_results_remain_sdk_instances():
    assert isinstance(DeclinedElicitation(), SDKDeclinedElicitation)
    assert isinstance(CancelledElicitation(), SDKCancelledElicitation)
    assert DeclinedElicitation().action == "decline"
    assert CancelledElicitation().action == "cancel"


async def test_truthiness_guard_refuses_declined_and_cancelled():
    mcp = FastMCP("guard")
    writes: list[str] = []

    @mcp.tool
    async def create(ctx: Context) -> str:
        result = await ctx.elicit("Create it?", response_type=bool)
        if not result or not result.data:
            return "refused"
        writes.append("created")
        return "created"

    for action in ("decline", "cancel"):

        async def handler(message, response_type, params, ctx, action=action):
            return ElicitResult(action=action)

        async with Client(mcp, mode="legacy", elicitation_handler=handler) as client:
            result = await client.call_tool("create")
            assert result.data == "refused"

    async def accept_no(message, response_type, params, ctx):
        return ElicitResult(action="accept", content={"value": False})

    async with Client(mcp, mode="legacy", elicitation_handler=accept_no) as client:
        assert (await client.call_tool("create")).data == "refused"

    async def accept_yes(message, response_type, params, ctx):
        return ElicitResult(action="accept", content={"value": True})

    async with Client(mcp, mode="legacy", elicitation_handler=accept_yes) as client:
        assert (await client.call_tool("create")).data == "created"

    assert writes == ["created"]
