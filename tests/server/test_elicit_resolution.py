"""Declarative elicitation: `Annotated[T, Elicit(...)]` parameters.

A parameter annotated this way is filled by asking the client rather than by
the model, and is hidden from the tool's input schema. The same annotated
function has to work on both protocol eras — batched into an
``InputRequiredResult`` on 2026-07-28 (where there is no back-channel), asked
in-process with ``ctx.elicit()`` on 2025-11-25 and earlier — because choosing
the transport is the whole reason for the declarative form.

The engine lives in the private ``fastmcp.server._elicit_resolution`` module,
which is expected to move into ``uncalled-for``; these tests exercise it
through the public ``fastmcp.elicitation.Elicit`` surface so the move stays
invisible.
"""

from typing import Annotated, Literal

import mcp_types
import pytest
from pydantic import BaseModel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.dependencies import Depends
from fastmcp.elicitation import Elicit
from fastmcp.exceptions import ToolError
from fastmcp.server._elicit_resolution import (
    NeedsInput,
    find_elicit_parameters,
    resolve_elicitations,
)
from fastmcp.server.middleware.middleware import Middleware
from fastmcp.tools.base import InputRequiredToolResult


class RecordAsks(Middleware):
    """Records the questions asked on each leg of a call."""

    def __init__(self) -> None:
        self.rounds: list[list[str]] = []

    @property
    def asks(self) -> int:
        return len(self.rounds)

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        if isinstance(result, InputRequiredToolResult):
            self.rounds.append(list(result.input_required.input_requests))
        return result


def accept(**fields):
    """An elicitation handler that accepts every question with fixed fields."""

    async def handler(message, response_type, params, ctx):
        return ElicitResult(action="accept", content=response_type(**fields))

    return handler


def accept_by_message(answers: dict[str, object], asked: list[str] | None = None):
    """Answer each question with the value whose key appears in the message."""

    async def handler(message, response_type, params, ctx):
        if asked is not None:
            asked.append(message)
        for marker, value in answers.items():
            if marker in message:
                return ElicitResult(action="accept", content=response_type(value=value))
        raise AssertionError(f"unexpected question: {message}")

    return handler


def refuse(action: Literal["decline", "cancel"] = "decline"):
    async def handler(message, response_type, params, ctx):
        return ElicitResult(action=action)

    return handler


class TestSchema:
    """An elicited parameter is not something the model supplies."""

    async def test_elicited_parameter_is_hidden(self):
        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            seats: int,
            destination: Annotated[str, Elicit("Where to?")],
        ) -> str:
            return f"{destination} x{seats}"

        tool = await mcp.get_tool("book")
        assert tool is not None
        assert list(tool.parameters["properties"]) == ["seats"]
        assert tool.parameters["required"] == ["seats"]

    async def test_optional_elicited_parameter_is_hidden(self):
        """A default makes the ask optional, not the parameter model-supplied."""
        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            seat: Annotated[str | None, Elicit("Window or aisle?")] = None,
        ) -> str:
            return seat or "none"

        tool = await mcp.get_tool("book")
        assert tool is not None
        assert tool.parameters.get("properties", {}) == {}


class TestModernProtocol:
    """2026-07-28: no back-channel, so asks ride `InputRequiredResult`."""

    async def test_single_question_completes(self):
        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where would you like to fly?")],
        ) -> str:
            return f"Booked {destination}"

        async with Client(
            mcp, mode="auto", elicitation_handler=accept(value="Paris")
        ) as client:
            assert client.protocol_version == "2026-07-28"
            result = await client.call_tool("book", {})

        assert result.data == "Booked Paris"

    async def test_body_does_not_run_until_answered(self):
        """The first leg resolves to the question, not to a partial execution."""
        runs: list[str] = []
        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where to?")],
        ) -> str:
            runs.append(destination)
            return destination

        async with Client(
            mcp, mode="auto", elicitation_handler=accept(value="Paris")
        ) as client:
            await client.call_tool("book", {})

        assert runs == ["Paris"]

    async def test_independent_questions_share_one_round(self):
        """Two asks that do not depend on each other go out together.

        This is the behavioural gain over a hand-written guard, which asks in
        whatever order the author wrote and pays a round trip for each.
        """
        mcp = FastMCP("x")
        recorder = RecordAsks()
        mcp.add_middleware(recorder)

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where to?")],
            date: Annotated[str, Elicit("When?")],
        ) -> str:
            return f"{destination} on {date}"

        handler = accept_by_message({"Where": "Paris", "When": "2026-08-01"})
        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("book", {})

        assert result.data == "Paris on 2026-08-01"
        assert recorder.asks == 1

    async def test_dependent_questions_take_a_round_each(self):
        """A question that quotes an unanswered one has to wait for it."""
        mcp = FastMCP("x")
        recorder = RecordAsks()
        mcp.add_middleware(recorder)

        def which_airport(destination: str) -> Elicit[str]:
            return Elicit(f"Which airport in {destination}?", response_type=str)

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where to?")],
            airport: Annotated[str, Elicit(which_airport)],
        ) -> str:
            return f"{destination}/{airport}"

        handler = accept_by_message({"Where": "Paris", "Which airport": "CDG"})
        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("book", {})

        assert result.data == "Paris/CDG"
        assert recorder.asks == 2

    async def test_dependent_question_quotes_the_earlier_answer(self):
        asked: list[str] = []
        mcp = FastMCP("x")

        def which_airport(destination: str) -> Elicit[str]:
            return Elicit(f"Which airport in {destination}?", response_type=str)

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where to?")],
            airport: Annotated[str, Elicit(which_airport)],
        ) -> str:
            return f"{destination}/{airport}"

        handler = accept_by_message(
            {"Where": "Paris", "Which airport": "CDG"}, asked=asked
        )
        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("book", {})

        assert asked == ["Where to?", "Which airport in Paris?"]
        assert result.data == "Paris/CDG"

    async def test_question_built_from_a_tool_argument(self):
        asked: list[str] = []
        mcp = FastMCP("x")

        def which_airport(destination: str) -> Elicit[str]:
            return Elicit(f"Which airport in {destination}?", response_type=str)

        @mcp.tool
        async def book(
            destination: str,
            airport: Annotated[str, Elicit(which_airport)],
        ) -> str:
            return f"{destination}/{airport}"

        handler = accept_by_message({"Which airport": "ORY"}, asked=asked)
        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("book", {"destination": "Paris"})

        assert asked == ["Which airport in Paris?"]
        assert result.data == "Paris/ORY"

    async def test_earlier_answers_survive_later_rounds(self):
        """An answer from round one is still there after round two asks again."""
        mcp = FastMCP("x")

        def follow_up(first: str) -> Elicit[str]:
            return Elicit(f"After {first}, then?", response_type=str)

        @mcp.tool
        async def chain(
            first: Annotated[str, Elicit("First?")],
            second: Annotated[str, Elicit(follow_up)],
        ) -> str:
            return f"{first}->{second}"

        handler = accept_by_message({"First": "a", "After a": "b"})
        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("chain", {})

        assert result.data == "a->b"


class TestDeclining:
    """A default marks the ask optional; without one, a decline stops the call."""

    @pytest.mark.parametrize("action", ["decline", "cancel"])
    async def test_optional_falls_back_to_the_default(
        self, action: Literal["decline", "cancel"]
    ):
        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            seat: Annotated[str | None, Elicit("Window or aisle?")] = None,
        ) -> str:
            return seat or "no preference"

        async with Client(
            mcp, mode="auto", elicitation_handler=refuse(action)
        ) as client:
            result = await client.call_tool("book", {})

        assert result.data == "no preference"

    @pytest.mark.parametrize("action", ["decline", "cancel"])
    async def test_required_fails_the_call(self, action: Literal["decline", "cancel"]):
        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where to?")],
        ) -> str:
            return destination

        async with Client(
            mcp, mode="auto", elicitation_handler=refuse(action)
        ) as client:
            with pytest.raises(ToolError, match="Cannot continue without"):
                await client.call_tool("book", {})


class TestHandshakeProtocol:
    """<= 2025-11-25: the back-channel exists, so asks happen in-process."""

    async def test_same_tool_works_unchanged(self):
        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where would you like to fly?")],
        ) -> str:
            return f"Booked {destination}"

        async with Client(
            mcp, mode="legacy", elicitation_handler=accept(value="Paris")
        ) as client:
            assert client.protocol_version != "2026-07-28"
            result = await client.call_tool("book", {})

        assert result.data == "Booked Paris"

    async def test_dependent_questions_still_ordered(self):
        asked: list[str] = []
        mcp = FastMCP("x")

        def which_airport(destination: str) -> Elicit[str]:
            return Elicit(f"Which airport in {destination}?", response_type=str)

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where to?")],
            airport: Annotated[str, Elicit(which_airport)],
        ) -> str:
            return f"{destination}/{airport}"

        handler = accept_by_message(
            {"Where": "Paris", "Which airport": "CDG"}, asked=asked
        )
        async with Client(mcp, mode="legacy", elicitation_handler=handler) as client:
            result = await client.call_tool("book", {})

        assert asked == ["Where to?", "Which airport in Paris?"]
        assert result.data == "Paris/CDG"

    async def test_optional_falls_back_to_the_default(self):
        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            seat: Annotated[str | None, Elicit("Window or aisle?")] = None,
        ) -> str:
            return seat or "no preference"

        async with Client(mcp, mode="legacy", elicitation_handler=refuse()) as client:
            result = await client.call_tool("book", {})

        assert result.data == "no preference"


class TestResponseTypes:
    """The annotated type is the schema, matching `ctx.elicit()`'s ergonomics."""

    async def test_model(self):
        class Airport(BaseModel):
            code: str

        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            airport: Annotated[Airport, Elicit("Which airport?")],
        ) -> str:
            return airport.code

        async with Client(
            mcp, mode="auto", elicitation_handler=accept(code="CDG")
        ) as client:
            result = await client.call_tool("book", {})

        assert result.data == "CDG"

    async def test_scalar_int(self):
        mcp = FastMCP("x")

        @mcp.tool
        async def book(seats: Annotated[int, Elicit("How many seats?")]) -> int:
            return seats * 2

        async with Client(
            mcp, mode="auto", elicitation_handler=accept(value=3)
        ) as client:
            result = await client.call_tool("book", {})

        assert result.data == 6


class TestInterop:
    """Elicited parameters sit alongside the other injected kinds."""

    async def test_with_context_and_depends(self):
        mcp = FastMCP("x")

        def house_style() -> str:
            return "!"

        @mcp.tool
        async def book(
            ctx: Context,
            destination: Annotated[str, Elicit("Where to?")],
            style: str = Depends(house_style),
        ) -> str:
            return f"{ctx.fastmcp.name}:{destination}{style}"

        async with Client(
            mcp, mode="auto", elicitation_handler=accept(value="Paris")
        ) as client:
            result = await client.call_tool("book", {})

        assert result.data == "x:Paris!"


class Airport(BaseModel):
    code: str


class TestConditionalResolvers:
    """A resolver returns `T | Elicit[T]` — a value means nobody is asked."""

    async def test_returning_a_value_asks_nothing(self):
        mcp = FastMCP("x")
        recorder = RecordAsks()
        mcp.add_middleware(recorder)

        def which_airport(destination: str) -> str | Elicit[str]:
            if destination == "London":
                return "LHR"  # only one option — no question
            return Elicit(f"Which airport in {destination}?", response_type=str)

        @mcp.tool
        async def book(
            destination: str,
            airport: Annotated[str, Elicit(which_airport)],
        ) -> str:
            return f"{destination}/{airport}"

        async def never(message, response_type, params, ctx):
            raise AssertionError(f"should not have asked: {message}")

        async with Client(mcp, mode="auto", elicitation_handler=never) as client:
            result = await client.call_tool("book", {"destination": "London"})

        assert result.data == "London/LHR"
        assert recorder.asks == 0

    async def test_the_same_resolver_still_asks_when_it_must(self):
        mcp = FastMCP("x")

        def which_airport(destination: str) -> str | Elicit[str]:
            if destination == "London":
                return "LHR"
            return Elicit(f"Which airport in {destination}?", response_type=str)

        @mcp.tool
        async def book(
            destination: str,
            airport: Annotated[str, Elicit(which_airport)],
        ) -> str:
            return f"{destination}/{airport}"

        handler = accept_by_message({"Which airport": "CDG"})
        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("book", {"destination": "Paris"})

        assert result.data == "Paris/CDG"

    async def test_resolver_beats_a_stale_answer_on_a_later_round(self):
        """The resolver re-runs every round, so a value it computes on round two
        wins over whatever the client echoed back."""
        mcp = FastMCP("x")
        known: list[str] = []

        def which_airport(destination: str) -> str | Elicit[str]:
            if known:
                return known[0]  # learned between rounds
            return Elicit(f"Which airport in {destination}?", response_type=str)

        @mcp.tool
        async def book(
            destination: str,
            date: Annotated[str, Elicit("When?")],
            airport: Annotated[str, Elicit(which_airport)],
        ) -> str:
            return f"{destination}/{airport}/{date}"

        async def handler(message, response_type, params, ctx):
            if "Which airport" in message:
                known.append("LHR")  # the profile gains one mid-conversation
                return ElicitResult(action="accept", content=response_type(value="CDG"))
            return ElicitResult(
                action="accept", content=response_type(value="2026-08-01")
            )

        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("book", {"destination": "Paris"})

        # The client answered "CDG", but by the next round the resolver knew "LHR".
        assert result.data == "Paris/LHR/2026-08-01"

    async def test_explicit_response_type_wins_over_the_annotation(self):
        mcp = FastMCP("x")

        def pick(destination: str) -> Airport | Elicit[Airport]:
            return Elicit(f"Which airport in {destination}?", response_type=Airport)

        @mcp.tool
        async def book(
            destination: str,
            airport: Annotated[Airport, Elicit(pick)],
        ) -> str:
            return airport.code

        async with Client(
            mcp, mode="auto", elicitation_handler=accept(code="CDG")
        ) as client:
            result = await client.call_tool("book", {"destination": "Paris"})

        assert result.data == "CDG"

    def test_declared_type_must_match_the_parameter(self):
        """Both types are visible at registration, so a disagreement is caught
        at import rather than as a validation failure on the answer."""
        mcp = FastMCP("x")

        def pick(destination: str) -> Airport | Elicit[Airport]:
            return Elicit("Which airport?", response_type=Airport)

        with pytest.raises(TypeError, match="declares it elicits"):

            @mcp.tool
            async def book(
                destination: str,
                airport: Annotated[str, Elicit(pick)],
            ) -> str:
                return airport


def accepted(**values) -> mcp_types.InputResponses:
    """The `input_responses` map for one leg: an accepted answer per key."""
    return {
        key: mcp_types.ElicitResult(action="accept", content={"value": value})
        for key, value in values.items()
    }


def questions(result) -> dict[str, str]:
    """The message shown for each key on one leg of a call."""
    leg = result.input_required
    assert leg is not None, "expected an ask, got a terminal result"
    assert leg.input_requests is not None
    asked: dict[str, str] = {}
    for key, request in leg.input_requests.items():
        assert isinstance(request, mcp_types.ElicitRequest)
        assert request.params is not None
        asked[key] = request.params.message
    return asked


def carried(result) -> str | None:
    """The opaque state to hand back on the next leg."""
    assert result.input_required is not None
    return result.input_required.request_state


class TestDrivingLegsByHand:
    """`` hands back each leg instead of resolving it,
    so a test can assert on the wire shape a client would actually receive."""

    async def test_each_leg_is_visible(self):
        mcp = FastMCP("x")

        def which_airport(destination: str) -> Elicit[str]:
            return Elicit(f"Which airport in {destination}?", response_type=str)

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where would you like to fly?")],
            airport: Annotated[str, Elicit(which_airport)],
        ) -> str:
            return f"Booked {destination}/{airport}"

        # No elicitation_handler — nothing drives the exchange but this test.
        async with Client(mcp) as client:
            first = await client.call_tool("book")
            assert questions(first) == {"destination": "Where would you like to fly?"}

            second = await client.call_tool(
                "book",
                input_responses=accepted(destination="Paris"),
                request_state=carried(first),
            )
            # Only the airport — the destination is not asked again.
            assert questions(second) == {"airport": "Which airport in Paris?"}

            final = await client.call_tool(
                "book",
                input_responses=accepted(airport="CDG"),
                request_state=carried(second),
            )

        assert final.input_required is None
        assert final.data == "Booked Paris/CDG"

    async def test_independent_questions_arrive_in_one_leg(self):
        mcp = FastMCP("x")

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where to?")],
            date: Annotated[str, Elicit("When?")],
        ) -> str:
            return f"{destination} on {date}"

        async with Client(mcp) as client:
            first = await client.call_tool("book")
            assert questions(first) == {
                "destination": "Where to?",
                "date": "When?",
            }

            final = await client.call_tool(
                "book",
                input_responses=accepted(destination="Paris", date="2026-08-01"),
                request_state=carried(first),
            )

        assert final.data == "Paris on 2026-08-01"

    async def test_a_resolver_that_knows_asks_nothing(self):
        mcp = FastMCP("x")

        def which_airport(destination: str) -> str | Elicit[str]:
            return (
                "LHR"
                if destination == "London"
                else Elicit("Which?", response_type=str)
            )

        @mcp.tool
        async def book(
            destination: str,
            airport: Annotated[str, Elicit(which_airport)],
        ) -> str:
            return f"{destination}/{airport}"

        async with Client(mcp) as client:
            result = await client.call_tool("book", {"destination": "London"})

        # Terminal on the first leg — there was never anything to ask.
        assert result.input_required is None
        assert result.data == "London/LHR"


class TestAskedOnce:
    """An answer already given satisfies its question on later rounds."""

    async def test_each_question_reaches_the_user_once(self):
        """Resolvers re-run every round, so without recall a three-round call
        would put the first question six times."""
        mcp = FastMCP("x")

        def second(a: str) -> Elicit[str]:
            return Elicit(f"second, given {a}?", response_type=str)

        def third(b: str) -> Elicit[str]:
            return Elicit(f"third, given {b}?", response_type=str)

        @mcp.tool
        async def chain(
            a: Annotated[str, Elicit("first?")],
            b: Annotated[str, Elicit(second)],
            c: Annotated[str, Elicit(third)],
        ) -> str:
            return f"{a}{b}{c}"

        asked: list[str] = []

        async def handler(message, response_type, params, ctx):
            asked.append(message)
            return ElicitResult(
                action="accept", content=response_type(value=str(len(asked)))
            )

        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("chain", {})

        assert result.data == "123"
        assert asked == ["first?", "second, given 1?", "third, given 2?"]


class TestOrdering:
    """Where and when a question is asked both fall out of the annotations."""

    async def test_independent_questions_keep_signature_order(self):
        """Signature order is the lever for presentation order — there is no other."""
        mcp = FastMCP("x")
        recorder = RecordAsks()
        mcp.add_middleware(recorder)

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where?")],
            date: Annotated[str, Elicit("When?")],
            seat: Annotated[str, Elicit("Window or aisle?")],
        ) -> str:
            return f"{destination}/{date}/{seat}"

        handler = accept_by_message(
            {"Where": "Paris", "When": "2026-08-01", "Window": "window"}
        )
        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            await client.call_tool("book", {})

        assert recorder.rounds == [["destination", "date", "seat"]]

    async def test_confirmation_quoting_details_waits_for_them(self):
        """A confirmation that names what it confirms is ordered by saying so,
        rather than by a parameter added to hold it back."""
        mcp = FastMCP("x")
        recorder = RecordAsks()
        mcp.add_middleware(recorder)

        def confirm(destination: str, date: str) -> Elicit[bool]:
            return Elicit(
                f"Book a flight to {destination} on {date}?", response_type=bool
            )

        @mcp.tool
        async def book(
            destination: Annotated[str, Elicit("Where?")],
            date: Annotated[str, Elicit("When?")],
            proceed: Annotated[bool, Elicit(confirm)],
        ) -> str:
            return f"Booked {destination}" if proceed else "Cancelled"

        asked: list[str] = []

        async def handler(message, response_type, params, ctx):
            asked.append(message)
            if "Where" in message:
                return ElicitResult(
                    action="accept", content=response_type(value="Paris")
                )
            if "When" in message:
                return ElicitResult(
                    action="accept", content=response_type(value="2026-08-01")
                )
            return ElicitResult(action="accept", content=response_type(value=True))

        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("book", {})

        assert recorder.rounds == [["destination", "date"], ["proceed"]]
        assert asked[-1] == "Book a flight to Paris on 2026-08-01?"
        assert result.data == "Booked Paris"


class TestQuestionDependencies:
    """A question is an ordinary function: it can declare its own dependencies."""

    async def test_question_resolves_its_own_depends(self):
        mcp = FastMCP("x")

        def house_prefix() -> str:
            return "[ACME]"

        def styled(prefix: str = Depends(house_prefix)) -> Elicit[str]:
            return Elicit(f"{prefix} Window or aisle?")

        @mcp.tool
        async def seat(choice: Annotated[str, Elicit(styled)]) -> str:
            return choice

        asked: list[str] = []
        handler = accept_by_message({"Window or aisle": "window"}, asked=asked)
        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("seat", {})

        assert asked == ["[ACME] Window or aisle?"]
        assert result.data == "window"

    async def test_async_question(self):
        mcp = FastMCP("x")

        async def ask_later(destination: str) -> Elicit[str]:
            return Elicit(f"Which airport in {destination}?")

        @mcp.tool
        async def book(
            destination: str,
            airport: Annotated[str, Elicit(ask_later)],
        ) -> str:
            return airport

        handler = accept_by_message({"Which airport": "CDG"})
        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("book", {"destination": "Paris"})

        assert result.data == "CDG"

    async def test_unresolvable_question_dependency_names_itself(self):
        """The DI engine reports a failed dependency rather than raising, so the
        sentinel has to be caught before it reaches the question as a value."""
        mcp = FastMCP("x")

        def needs_a_tool_argument(destination: str) -> str:
            return destination

        def styled(place: str = Depends(needs_a_tool_argument)) -> Elicit[str]:
            return Elicit(f"Where in {place}?")

        @mcp.tool
        async def book(
            destination: str,
            airport: Annotated[str, Elicit(styled)],
        ) -> str:
            return airport

        async with Client(
            mcp, mode="auto", elicitation_handler=accept(value="CDG")
        ) as client:
            with pytest.raises(ToolError, match="depends on 'place'"):
                await client.call_tool("book", {"destination": "Paris"})


class _StubContext:
    """The three things resolution reads off a live context."""

    def __init__(
        self,
        *,
        modern: bool = True,
        request_state: str | None = None,
        input_responses: dict | None = None,
    ) -> None:
        self.request_state = request_state
        self.input_responses = input_responses
        self._modern = modern

    def _is_modern_protocol(self) -> bool:
        return self._modern


async def _ask_once(specs, arguments, context):
    """Run one round, returning either the values or the raised question."""
    try:
        return await resolve_elicitations(specs, arguments, context), None
    except NeedsInput as needs_input:
        return None, needs_input


class TestQuestionDigest:
    """An answer only counts for the exact question it was shown against."""

    def _specs(self, question):
        def book(seat: Annotated[str, Elicit(question)]) -> str:
            return seat

        return find_elicit_parameters(book)

    async def test_answer_to_a_changed_question_is_re_asked(self):
        """A redeploy that rewords a question must not reuse the old answer."""
        first = self._specs("Window or aisle?")
        _, asked = await _ask_once(first, {}, _StubContext())
        assert asked is not None

        reply = {
            "seat": mcp_types.ElicitResult(action="accept", content={"value": "W"})
        }

        # Same wording: the reply is accepted.
        same = await _ask_once(
            first,
            {},
            _StubContext(request_state=asked.request_state, input_responses=reply),
        )
        assert same[0] == {"seat": "W"}

        # Reworded: the reply is dropped and the new question goes out instead.
        reworded = self._specs("Which seat would you prefer?")
        values, again = await _ask_once(
            reworded,
            {},
            _StubContext(request_state=asked.request_state, input_responses=reply),
        )
        assert values is None
        assert again is not None
        assert "seat" in again.input_requests

    async def test_unreadable_state_is_treated_as_no_progress(self):
        """Drift inside a fleet re-asks rather than misreading an older layout."""
        specs = self._specs("Window or aisle?")
        values, asked = await _ask_once(
            specs, {}, _StubContext(request_state='{"v":999,"answers":{}}')
        )
        assert values is None
        assert asked is not None


class TestRegistrationErrors:
    """Signature mistakes fail at registration, not on the first call."""

    def test_question_asks_for_an_unknown_name(self):
        mcp = FastMCP("x")

        def question(nonexistent: str) -> Elicit[str]:
            return Elicit(nonexistent)

        with pytest.raises(TypeError, match="not a parameter of the function"):

            @mcp.tool
            async def book(
                airport: Annotated[str, Elicit(question)],
            ) -> str:
                return airport

    def test_cyclic_questions(self):
        mcp = FastMCP("x")

        def needs_b(b: str) -> Elicit[str]:
            return Elicit(b)

        def needs_a(a: str) -> Elicit[str]:
            return Elicit(a)

        with pytest.raises(TypeError, match="form a cycle"):

            @mcp.tool
            async def book(
                a: Annotated[str, Elicit(needs_b)],
                b: Annotated[str, Elicit(needs_a)],
            ) -> str:
                return a + b

    def test_mixing_with_a_hand_returned_ask(self):
        """One call, one input channel — the two ways of asking cannot share it."""
        import mcp_types

        mcp = FastMCP("x")

        with pytest.raises(TypeError, match="one channel for gathering input"):

            @mcp.tool
            async def book(
                destination: Annotated[str, Elicit("Where to?")],
            ) -> str | mcp_types.InputRequiredResult:
                return destination

    def test_marker_buried_out_of_reach(self):
        """A marker somewhere the framework cannot honour it fails loudly."""
        mcp = FastMCP("x")

        with pytest.raises(TypeError, match="wraps Elicit"):

            @mcp.tool
            async def book(
                destinations: list[Annotated[str, Elicit("Where?")]],
            ) -> str:
                return ",".join(destinations)

    @pytest.mark.parametrize(
        "annotation",
        [
            Annotated[str | None, Elicit("Window or aisle?")],
            Annotated[str, Elicit("Window or aisle?")] | None,
        ],
        ids=["none-inside", "none-outside"],
    )
    async def test_optional_spellings_are_equivalent(self, annotation):
        """Python 3.10 applies implicit-Optional to a `= None` parameter, so the
        two spellings are indistinguishable there and must behave alike."""
        mcp = FastMCP("x")

        @mcp.tool
        async def book(seat: annotation = None) -> str:
            return seat or "no preference"

        tool = await mcp.get_tool("book")
        assert tool is not None
        assert tool.parameters.get("properties", {}) == {}

        async with Client(mcp, mode="auto", elicitation_handler=refuse()) as client:
            result = await client.call_tool("book", {})

        assert result.data == "no preference"
