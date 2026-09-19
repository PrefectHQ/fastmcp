"""Tool search ranked by TypeSafe's Jev.

Jev is a System One model: it does not generate text. A request carries a
``state`` and a map of typed questions, every question is judged against the
same state in parallel, and each answer is a probability distribution over
options the caller defined. That makes it a natural ranker for a tool
catalog: the query is the state, the tool names are the options, and the
probabilities are the ranking.

The transform follows the shape of TypeSafe's skill-suggestion cookbook
(https://docs.typesafe.ai/cookbooks/skill_suggestion): a cheap wide pass over
the whole catalog on one-line summaries, then a close read of a shortlist
with each tool's full description and parameters. The close read asks two
kinds of question. A Choice decides *which* candidate fits best and orders
the results. One Noul per candidate decides *whether* it does what the query
asks at all, so a query nothing serves comes back empty instead of returning
the least-wrong tool.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Protocol

from fastmcp.server.context import Context
from fastmcp.server.transforms.search.base import (
    BaseSearchTransform,
    SearchResultSerializer,
    serialize_tools_for_output_markdown,
)
from fastmcp.tools.base import Tool

WIDE_INSTRUCTIONS = (
    "Which of these tools, if any, is the right one to call to carry out the "
    "user's request in `request`? Each option is a tool name; its description "
    "summarizes what the tool does."
)
RERANK_INSTRUCTIONS = (
    "Exactly one of these tools is the right one to call for the user's request "
    "in `request`. Which one? Read what each tool actually does and what "
    "parameters it takes, not just its name."
)


class SystemOneClient(Protocol):
    """The slice of ``typesafe_sdk.AsyncTypeSafeClient`` the transform uses."""

    async def system_one(self, state: Any, questions: Any) -> Any: ...


def _summary(tool: Tool, limit: int) -> str:
    """The first paragraph of a tool's description, or its name when it has none."""
    text = (tool.description or "").strip()
    first = text.split("\n\n", 1)[0].strip() or tool.name.replace("_", " ")
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _detail(tool: Tool, limit: int) -> str:
    """A tool's full description and parameter list, rendered as markdown."""
    text = serialize_tools_for_output_markdown([tool])
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _catalog_hash(tools: Sequence[Tool]) -> str:
    key = "|".join(sorted(f"{t.name}\n{t.description or ''}" for t in tools))
    return hashlib.sha256(key.encode()).hexdigest()


def _fit_id(name: str) -> str:
    return f"fits::{name}"


class JevSearchTransform(BaseSearchTransform):
    """Search transform that ranks tools with TypeSafe's Jev.

    Experimental: the ranking parameters may change. Requires the ``jev``
    extra (``pip install "fastmcp[jev]"``) and a TypeSafe API key, read from
    ``TYPESAFE_API_KEY`` unless ``api_key`` or ``client`` is given.

    Args:
        model: The TypeSafe model name. ``jev-latest`` follows releases;
            pin a versioned id once you have tuned ``fit_threshold``.
        api_key: TypeSafe API key. Defaults to ``TYPESAFE_API_KEY``.
        client: A ready ``AsyncTypeSafeClient`` (or anything with an async
            ``system_one``) to use instead of building one.
        shortlist: How many candidates the wide pass carries into the close
            read, per chunk of the catalog.
        fit_threshold: A candidate whose "does this tool do what the request
            asks" probability falls below this is dropped from the results.
            Tune it against queries from your own users.
        chunk_size: Catalog size above which the wide pass is split into
            several concurrent requests, each ranking one chunk.
        summary_chars: Characters of description per tool in the wide pass.
        detail_chars: Characters of rendered description and parameters per
            tool in the close read.
        max_results, always_visible, search_tool_name, call_tool_name,
            search_result_serializer: As on every search transform.
    """

    def __init__(
        self,
        *,
        model: str = "jev-latest",
        api_key: str | None = None,
        client: SystemOneClient | None = None,
        shortlist: int = 8,
        fit_threshold: float = 0.3,
        chunk_size: int = 150,
        summary_chars: int = 160,
        detail_chars: int = 1200,
        max_results: int = 5,
        always_visible: list[str] | None = None,
        search_tool_name: str = "search_tools",
        call_tool_name: str = "call_tool",
        search_result_serializer: SearchResultSerializer | None = None,
    ) -> None:
        super().__init__(
            max_results=max_results,
            always_visible=always_visible,
            search_tool_name=search_tool_name,
            call_tool_name=call_tool_name,
            search_result_serializer=search_result_serializer,
        )
        if shortlist < 1 or chunk_size < 1:
            raise ValueError("shortlist and chunk_size must be at least 1")
        if not 0 <= fit_threshold <= 1:
            raise ValueError("fit_threshold must be between 0 and 1")
        self._model = model
        self._api_key = api_key
        self._client = client
        self._shortlist = shortlist
        self._fit_threshold = fit_threshold
        self._chunk_size = chunk_size
        self._summary_chars = summary_chars
        self._detail_chars = detail_chars
        self._summaries: dict[str, str] = {}
        self._details: dict[str, str] = {}
        self._last_hash = ""

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    def _get_client(self) -> SystemOneClient:
        if self._client is None:
            try:
                from typesafe_sdk import AsyncTypeSafeClient
            except ImportError as e:
                raise ImportError(
                    "JevSearchTransform needs the typesafe-sdk package: "
                    'install the jev extra with `pip install "fastmcp[jev]"`'
                ) from e
            self._client = AsyncTypeSafeClient(api_key=self._api_key, model=self._model)
        return self._client

    # ------------------------------------------------------------------
    # Synthetic search tool
    # ------------------------------------------------------------------

    def _make_search_tool(self) -> Tool:
        transform = self

        async def search_tools(
            query: Annotated[
                str, "What you need to do, in natural language; not keywords"
            ],
            ctx: Context = None,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        ) -> str | list[dict[str, Any]]:
            """Find the tools that carry out a request.

            Returns the best-fitting tool definitions, best first, in the
            same format as list_tools. Returns nothing when no tool fits.
            """
            hidden = await transform._get_visible_tools(ctx)
            results = await transform._search(hidden, query)
            return await transform._render_results(results)

        return Tool.from_function(fn=search_tools, name=self._search_tool_name)

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def _refresh_texts(self, tools: Sequence[Tool]) -> None:
        current = _catalog_hash(tools)
        if current == self._last_hash:
            return
        self._summaries = {t.name: _summary(t, self._summary_chars) for t in tools}
        self._details = {t.name: _detail(t, self._detail_chars) for t in tools}
        self._last_hash = current

    async def _rank_chunk(
        self, query: str, names: Sequence[str]
    ) -> list[tuple[str, float]]:
        """One wide request: rank every tool in a chunk by summary."""
        response = await self._get_client().system_one(
            state={"request": query},
            questions={
                "which": {
                    "type": "choice",
                    "instructions": WIDE_INSTRUCTIONS,
                    "criteria": {name: self._summaries[name] for name in names},
                }
            },
        )
        probabilities: Mapping[str, float] = response.answers["which"].probabilities
        ranked = sorted(probabilities.items(), key=lambda kv: -kv[1])
        return ranked[: self._shortlist]

    async def _rerank(
        self, query: str, names: Sequence[str]
    ) -> list[tuple[str, float]]:
        """One close-read request over the shortlist: which fits best, and
        whether each fits at all. Returns the names that fit, best first."""
        questions: dict[str, Any] = {
            "which": {
                "type": "choice",
                "instructions": RERANK_INSTRUCTIONS,
                "criteria": {name: self._details[name] for name in names},
            }
        }
        for name in names:
            questions[_fit_id(name)] = {
                "type": "noul",
                "instructions": (
                    f"Does the tool `{name}` do the specific thing the user's "
                    f"request in `request` asks for? The tool is described as: "
                    f"{self._summaries[name]}"
                ),
            }
        response = await self._get_client().system_one(
            state={"request": query}, questions=questions
        )
        answers = response.answers
        probabilities: Mapping[str, float] = answers["which"].probabilities
        fits = [
            (name, probabilities.get(name, 0.0))
            for name in names
            if answers[_fit_id(name)].noul >= self._fit_threshold
        ]
        return sorted(fits, key=lambda kv: -kv[1])

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        if not tools or not query.strip():
            return []
        self._refresh_texts(tools)
        by_name = {t.name: t for t in tools}
        names = list(by_name)

        if len(names) > self._shortlist:
            chunks = [
                names[i : i + self._chunk_size]
                for i in range(0, len(names), self._chunk_size)
            ]
            ranked_chunks = await asyncio.gather(
                *(self._rank_chunk(query, chunk) for chunk in chunks)
            )
            # Probabilities from different chunks are not comparable, so every
            # chunk's shortlist goes to the close read rather than a merged cut.
            candidates = [name for ranked in ranked_chunks for name, _ in ranked]
        else:
            candidates = names

        fitting = await self._rerank(query, candidates)
        return [by_name[name] for name, _ in fitting[: self._max_results]]
