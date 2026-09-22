"""JevSearchTransform: request shape, two-pass ranking, thresholds, chunking.

Jev is driven through a fake client so the suite needs neither the SDK nor
a key. The fake records every request and answers from a table of
probabilities keyed by tool name.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.experimental.transforms.jev_search import (
    MAX_CHOICE_OPTIONS,
    RERANK_INSTRUCTIONS,
    WIDE_INSTRUCTIONS,
    JevSearchTransform,
)
from fastmcp.tools.base import Tool


@dataclass
class _Answer:
    choice: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    noul: float = 0.0


@dataclass
class _Response:
    answers: dict[str, _Answer]


@dataclass
class FakeJev:
    """Answers Choice questions from ``weights`` and Nouls from ``fits``.

    Options missing from ``weights`` get a small equal share; Nouls missing
    from ``fits`` are answered as a confident yes. ``delay`` yields to the
    event loop inside each request so concurrent searches interleave.
    """

    weights: Mapping[str, float] = field(default_factory=dict)
    fits: Mapping[str, float] = field(default_factory=dict)
    delay: float = 0.0
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def system_one(self, state: Any, questions: Any) -> _Response:
        self.requests.append({"state": state, "questions": dict(questions)})
        if self.delay:
            await asyncio.sleep(self.delay)
        answers: dict[str, _Answer] = {}
        for qid, question in questions.items():
            if question["type"] == "choice":
                raw = {
                    name: self.weights.get(name, 0.01) for name in question["criteria"]
                }
                total = sum(raw.values())
                probabilities = {name: value / total for name, value in raw.items()}
                best = max(probabilities, key=probabilities.__getitem__)
                answers[qid] = _Answer(
                    choice=best,
                    probabilities=probabilities,
                    confidence=probabilities[best],
                )
            else:
                name = qid.removeprefix("fits::")
                answers[qid] = _Answer(noul=self.fits.get(name, 0.9))
        return _Response(answers=answers)


def _tool(name: str, doc: str | None = None, params: tuple[str, ...] = ("query",)):
    def fn(query: str = "") -> str:
        return f"{name}:{query}"

    fn.__name__ = name
    fn.__doc__ = (
        doc
        if doc is not None
        else f"The {name.replace('_', ' ')} tool.\n\nMore detail about {name}."
    )
    parameters = {
        "type": "object",
        "properties": {p: {"type": "string"} for p in params},
        "required": list(params),
    }
    return Tool.from_function(fn=fn, name=name, description=fn.__doc__).model_copy(
        update={"parameters": parameters}
    )


def _server(*names: str) -> FastMCP:
    mcp = FastMCP("test")
    for name in names:

        def make(name: str):
            def tool(query: str) -> str:
                return f"{name}:{query}"

            tool.__name__ = name
            tool.__doc__ = (
                f"The {name.replace('_', ' ')} tool.\n\nMore detail about {name}."
            )
            return tool

        mcp.tool(make(name))
    return mcp


async def _search(mcp: FastMCP, query: str) -> list[str]:
    async with Client(mcp) as client:
        result = await client.call_tool("search_tools", {"query": query})
    assert result.structured_content is not None
    return [tool["name"] for tool in result.structured_content["result"]]


class TestListing:
    async def test_list_tools_shows_only_synthetic_tools(self):
        mcp = _server("send_email", "delete_record")
        mcp.add_transform(JevSearchTransform(client=FakeJev()))
        names = {t.name for t in await mcp.list_tools()}
        assert names == {"search_tools", "call_tool"}

    async def test_search_tool_takes_a_query(self):
        mcp = _server("send_email")
        mcp.add_transform(JevSearchTransform(client=FakeJev()))
        search = await mcp.get_tool("search_tools")
        assert search is not None
        assert "query" in search.parameters["properties"]


class TestSmallCatalog:
    """A catalog that fits the close read goes there directly."""

    async def test_single_request_orders_by_choice_probability(self):
        jev = FakeJev(weights={"delete_record": 5, "send_email": 3, "add": 1})
        mcp = _server("add", "send_email", "delete_record")
        mcp.add_transform(JevSearchTransform(client=jev, shortlist=8))

        assert await _search(mcp, "remove a row") == [
            "delete_record",
            "send_email",
            "add",
        ]
        assert len(jev.requests) == 1

    async def test_close_read_puts_summaries_in_state_and_detail_in_criteria(self):
        jev = FakeJev()
        mcp = _server("send_email", "delete_record")
        mcp.add_transform(JevSearchTransform(client=jev))
        await _search(mcp, "email someone")

        [request] = jev.requests
        state = request["state"]
        assert state["request"] == "email someone"
        # the description a noul refers to lives in state, not in the question
        assert state["tools"] == {
            "send_email": "The send email tool.",
            "delete_record": "The delete record tool.",
        }
        which = request["questions"]["which"]
        assert which["instructions"] == RERANK_INSTRUCTIONS
        assert set(which["criteria"]) == {"send_email", "delete_record"}
        # the close read sees the rendered tool, not just the first line
        assert "More detail about send_email" in which["criteria"]["send_email"]
        assert "`query`" in which["criteria"]["send_email"]
        for name in ("send_email", "delete_record"):
            noul = request["questions"][f"fits::{name}"]
            assert noul["type"] == "noul"
            assert f"`tools.{name}`" in noul["instructions"]
            assert "The " not in noul["instructions"]

    async def test_tools_below_fit_threshold_are_dropped(self):
        jev = FakeJev(
            weights={"send_email": 5, "delete_record": 4},
            fits={"send_email": 0.8, "delete_record": 0.1},
        )
        mcp = _server("send_email", "delete_record")
        mcp.add_transform(JevSearchTransform(client=jev, fit_threshold=0.3))
        assert await _search(mcp, "email someone") == ["send_email"]

    async def test_nothing_fits_returns_empty(self):
        jev = FakeJev(fits={"send_email": 0.05, "delete_record": 0.02})
        mcp = _server("send_email", "delete_record")
        mcp.add_transform(JevSearchTransform(client=jev))
        assert await _search(mcp, "book a flight") == []

    async def test_max_results_caps_the_fitting_tools(self):
        jev = FakeJev(weights={"a": 3, "b": 2, "c": 1})
        mcp = _server("a", "b", "c")
        mcp.add_transform(JevSearchTransform(client=jev, max_results=2))
        assert await _search(mcp, "anything") == ["a", "b"]


class TestLargeCatalog:
    """Above the close-read size, wide passes per chunk narrow the field."""

    async def test_wide_pass_is_chunked_and_shortlisted(self):
        names = [f"tool_{i:02d}" for i in range(7)]
        jev = FakeJev(
            weights={
                "tool_06": 9,
                "tool_01": 5,
                "tool_02": 3,
                "tool_04": 2,
                "tool_05": 1.5,
            }
        )
        mcp = _server(*names)
        # close read holds 3 * shortlist = 6; seven tools need a wide pass
        mcp.add_transform(
            JevSearchTransform(client=jev, shortlist=2, chunk_size=3, max_results=3)
        )

        assert await _search(mcp, "the sixth thing") == [
            "tool_06",
            "tool_01",
            "tool_02",
        ]

        wide = jev.requests[:-1]
        close = jev.requests[-1]
        assert [sorted(r["questions"]["which"]["criteria"]) for r in wide] == [
            ["tool_00", "tool_01", "tool_02"],
            ["tool_03", "tool_04", "tool_05"],
            ["tool_06"],
        ]
        assert all(
            r["questions"]["which"]["instructions"] == WIDE_INSTRUCTIONS for r in wide
        )
        assert all(len(r["questions"]) == 1 for r in wide)
        assert all(r["state"] == {"request": "the sixth thing"} for r in wide)
        # two per chunk survive the wide pass; the third chunk only has one
        assert sorted(close["questions"]["which"]["criteria"]) == [
            "tool_01",
            "tool_02",
            "tool_04",
            "tool_05",
            "tool_06",
        ]

    async def test_wide_pass_repeats_until_the_close_read_fits(self):
        names = [f"t{i:02d}" for i in range(30)]
        jev = FakeJev(weights={"t29": 9})
        mcp = _server(*names)
        mcp.add_transform(JevSearchTransform(client=jev, shortlist=2, chunk_size=5))

        assert (await _search(mcp, "the last one"))[0] == "t29"
        # round one: 6 chunks of 5 keep 12; round two: 3 chunks of 5 keep 6;
        # six fits the close read (3 * shortlist), so one more request
        assert len(jev.requests) == 6 + 3 + 1
        close = jev.requests[-1]
        assert len(close["questions"]["which"]["criteria"]) == 6

    async def test_close_read_never_exceeds_choice_limit(self):
        names = [f"t{i:03d}" for i in range(MAX_CHOICE_OPTIONS + 1)]
        jev = FakeJev()
        mcp = _server(*names)
        mcp.add_transform(JevSearchTransform(client=jev, shortlist=100, chunk_size=150))

        await _search(mcp, "x")

        choice_sizes = [
            len(request["questions"]["which"]["criteria"]) for request in jev.requests
        ]
        assert choice_sizes == [150, 106, 200]
        assert max(choice_sizes) <= MAX_CHOICE_OPTIONS

    async def test_wide_pass_uses_first_paragraph_only(self):
        jev = FakeJev()
        mcp = _server(*(f"t{i}" for i in range(4)))
        mcp.add_transform(JevSearchTransform(client=jev, shortlist=1))
        await _search(mcp, "x")
        summary = jev.requests[0]["questions"]["which"]["criteria"]["t0"]
        assert summary == "The t0 tool."


class TestSinglePass:
    """``close_read=False`` ranks and filters in one request per chunk."""

    async def test_one_request_with_a_fit_question_per_tool(self):
        jev = FakeJev(
            weights={"delete_record": 5, "send_email": 3},
            fits={"add": 0.1},
        )
        mcp = _server("add", "send_email", "delete_record")
        mcp.add_transform(JevSearchTransform(client=jev, close_read=False))

        assert await _search(mcp, "remove a row") == ["delete_record", "send_email"]
        [request] = jev.requests
        assert request["questions"]["which"]["instructions"] == WIDE_INSTRUCTIONS
        assert set(request["questions"]) == {
            "which",
            "fits::add",
            "fits::send_email",
            "fits::delete_record",
        }
        # summaries only: the rendered parameter list never goes out
        assert "`query`" not in request["questions"]["which"]["criteria"]["add"]
        assert set(request["state"]["tools"]) == {"add", "send_email", "delete_record"}

    async def test_large_catalog_is_still_one_round_trip(self):
        names = [f"t{i:02d}" for i in range(30)]
        jev = FakeJev(weights={"t29": 9})
        mcp = _server(*names)
        mcp.add_transform(
            JevSearchTransform(client=jev, close_read=False, chunk_size=10)
        )
        assert (await _search(mcp, "the last one"))[0] == "t29"
        assert len(jev.requests) == 3
        assert all(len(r["questions"]) == 11 for r in jev.requests)


class TestRenderedText:
    def test_summary_and_detail_are_truncated(self):
        transform = JevSearchTransform(
            client=FakeJev(), summary_chars=12, detail_chars=40
        )
        tool = _tool("send_email", "Send an email to the given recipient.")
        summaries, details = transform._render([tool])
        assert summaries["send_email"] == "Send an ema…"
        assert len(summaries["send_email"]) == 12
        assert len(details["send_email"]) == 40
        assert details["send_email"].endswith("…")

    def test_parameter_change_rerenders_the_detail(self):
        transform = JevSearchTransform(client=FakeJev())
        before = _tool("send_email", "Send mail.", params=("to",))
        after = _tool("send_email", "Send mail.", params=("to", "subject"))
        _, first = transform._render([before])
        _, second = transform._render([after])
        assert "`subject`" not in first["send_email"]
        assert "`subject`" in second["send_email"]


class TestConcurrency:
    async def test_searches_with_different_catalogs_do_not_interfere(self):
        """Two sessions can see different tools and search at the same time;
        neither may lose the text it needs mid-search."""
        jev = FakeJev(weights={"tool_11": 9, "tool_03": 5}, delay=0.01)
        transform = JevSearchTransform(client=jev, shortlist=2, chunk_size=4)
        tools = [_tool(f"tool_{i:02d}") for i in range(12)]

        full, partial = await asyncio.gather(
            transform._search(tools, "the eleventh"),
            transform._search(tools[:6], "the third"),
        )
        assert [t.name for t in full][0] == "tool_11"
        assert [t.name for t in partial][0] == "tool_03"


class TestCatalogChanges:
    async def test_texts_refresh_when_a_tool_is_added(self):
        jev = FakeJev()
        mcp = _server("send_email")
        mcp.add_transform(JevSearchTransform(client=jev))
        await _search(mcp, "x")

        @mcp.tool
        def delete_record(record_id: str) -> str:
            """Delete a record."""
            return record_id

        await _search(mcp, "x")
        assert set(jev.requests[-1]["questions"]["which"]["criteria"]) == {
            "send_email",
            "delete_record",
        }


class TestCallThrough:
    async def test_discovered_tool_runs_through_the_proxy(self):
        jev = FakeJev(weights={"send_email": 9})
        mcp = _server("send_email", "delete_record")
        mcp.add_transform(JevSearchTransform(client=jev))
        async with Client(mcp) as client:
            found = await client.call_tool("search_tools", {"query": "email"})
            assert found.structured_content is not None
            name = found.structured_content["result"][0]["name"]
            result = await client.call_tool(
                "call_tool", {"name": name, "arguments": {"query": "hi"}}
            )
        assert result.data == "send_email:hi"


class TestConfiguration:
    def test_rejects_bad_settings(self):
        with pytest.raises(ValueError):
            JevSearchTransform(client=FakeJev(), fit_threshold=1.5)
        with pytest.raises(ValueError):
            JevSearchTransform(client=FakeJev(), shortlist=0)
        with pytest.raises(ValueError, match="less than chunk_size"):
            JevSearchTransform(client=FakeJev(), shortlist=8, chunk_size=5)
        JevSearchTransform(
            client=FakeJev(), close_read=False, shortlist=8, chunk_size=5
        )
        with pytest.raises(ValueError, match="255"):
            JevSearchTransform(client=FakeJev(), chunk_size=300)
        with pytest.raises(ValueError):
            JevSearchTransform(client=FakeJev(), summary_chars=0)

    def test_missing_api_key_fails_at_construction(self, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
            JevSearchTransform()

    def test_api_key_from_environment_is_accepted(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "key-from-env")
        assert JevSearchTransform()._api_key == "key-from-env"

    async def test_missing_sdk_is_a_clear_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
        with pytest.raises(ImportError, match="typesafe-sdk"):
            await JevSearchTransform(api_key="k")._get_client()

    async def test_empty_query_makes_no_request(self):
        jev = FakeJev()
        mcp = _server("send_email")
        mcp.add_transform(JevSearchTransform(client=jev))
        assert await _search(mcp, "   ") == []
        assert jev.requests == []
