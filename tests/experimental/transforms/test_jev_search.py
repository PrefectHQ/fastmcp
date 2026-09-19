"""JevSearchTransform: request shape, two-pass ranking, thresholds, chunking.

Jev is driven through a fake client so the suite needs neither the SDK nor
a key. The fake records every request and answers from a table of
probabilities keyed by tool name.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.experimental.transforms.jev_search import (
    RERANK_INSTRUCTIONS,
    WIDE_INSTRUCTIONS,
    JevSearchTransform,
)


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
    from ``fits`` are answered as a confident yes.
    """

    weights: Mapping[str, float] = field(default_factory=dict)
    fits: Mapping[str, float] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def system_one(
        self, state: Any, questions: Mapping[str, Any], **kwargs: Any
    ) -> _Response:
        self.requests.append({"state": state, "questions": dict(questions)})
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
    """A catalog no larger than the shortlist goes straight to the close read."""

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

    async def test_close_read_carries_detail_and_one_noul_per_tool(self):
        jev = FakeJev()
        mcp = _server("send_email", "delete_record")
        mcp.add_transform(JevSearchTransform(client=jev))
        await _search(mcp, "email someone")

        [request] = jev.requests
        assert request["state"] == {"request": "email someone"}
        which = request["questions"]["which"]
        assert which["instructions"] == RERANK_INSTRUCTIONS
        assert set(which["criteria"]) == {"send_email", "delete_record"}
        # the close read sees the rendered tool, not just the first line
        assert "More detail about send_email" in which["criteria"]["send_email"]
        assert "`query`" in which["criteria"]["send_email"]
        for name in ("send_email", "delete_record"):
            noul = request["questions"][f"fits::{name}"]
            assert noul["type"] == "noul"
            assert f"`{name}`" in noul["instructions"]

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
    """Above the shortlist, a wide pass per chunk feeds the close read."""

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
        # two per chunk survive the wide pass; the third chunk only has one
        assert sorted(close["questions"]["which"]["criteria"]) == [
            "tool_01",
            "tool_02",
            "tool_04",
            "tool_05",
            "tool_06",
        ]

    async def test_wide_pass_uses_first_paragraph_only(self):
        jev = FakeJev()
        mcp = _server(*(f"t{i}" for i in range(3)))
        mcp.add_transform(JevSearchTransform(client=jev, shortlist=1))
        await _search(mcp, "x")
        summary = jev.requests[0]["questions"]["which"]["criteria"]["t0"]
        assert summary == "The t0 tool."


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
    def test_rejects_bad_thresholds(self):
        with pytest.raises(ValueError):
            JevSearchTransform(client=FakeJev(), fit_threshold=1.5)
        with pytest.raises(ValueError):
            JevSearchTransform(client=FakeJev(), shortlist=0)

    def test_missing_sdk_is_a_clear_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
        with pytest.raises(ImportError, match="typesafe-sdk"):
            JevSearchTransform()._get_client()

    async def test_empty_query_makes_no_request(self):
        jev = FakeJev()
        mcp = _server("send_email")
        mcp.add_transform(JevSearchTransform(client=jev))
        assert await _search(mcp, "   ") == []
        assert jev.requests == []
