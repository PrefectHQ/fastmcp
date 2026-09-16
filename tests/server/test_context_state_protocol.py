"""Context state follows the negotiated protocol's connection lifetime."""

from typing import Literal

import pytest

from fastmcp import Client, Context, FastMCP


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_context_state_lifetime(mode: Literal["auto", "legacy"]):
    server = FastMCP("context-state")

    @server.tool
    async def remember(value: str, ctx: Context) -> str:
        session_id = ctx.session_id
        await ctx.set_state("value", value)
        assert await ctx.get_state("value") == value
        assert ctx.session_id == session_id
        return session_id

    @server.tool
    async def recall(ctx: Context) -> dict:
        return {"id": ctx.session_id, "value": await ctx.get_state("value")}

    async with Client(server, mode=mode) as first:
        first_id = (await first.call_tool("remember", {"value": "first"})).data

        async with Client(server, mode=mode) as second:
            empty = (await second.call_tool("recall")).data
            assert empty["value"] is None
            assert empty["id"] != first_id
            second_id = (await second.call_tool("remember", {"value": "second"})).data
            assert second_id != first_id

            first_state = (await first.call_tool("recall")).data
            second_state = (await second.call_tool("recall")).data

            if mode == "legacy":
                assert first_state == {"id": first_id, "value": "first"}
                assert second_state == {"id": second_id, "value": "second"}
            else:
                assert first_state["value"] is None
                assert second_state["value"] is None
                assert (
                    len({first_id, second_id, first_state["id"], second_state["id"]})
                    == 4
                )

    async with Client(server, mode=mode) as reconnected:
        state = (await reconnected.call_tool("recall")).data
        assert state["value"] is None
        assert state["id"] not in {first_id, second_id}
