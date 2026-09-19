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
import json
import os
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
    "Which of these tools is the right one to call to carry out the user's "
    "request in `request`? Each option is a tool name; its description "
    "summarizes what the tool does."
)
RERANK_INSTRUCTIONS = (
    "Exactly one of these tools is the right one to call for the user's request "
    "in `request`. Which one? Read what each tool actually does and what "
    "parameters it takes, not just its name."
)

# A Choice question accepts at most this many options
# (https://docs.typesafe.ai/primitives/choice).
MAX_CHOICE_OPTIONS = 255
API_KEY_ENV = "TYPESAFE_API_KEY"


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


def _fingerprint(tool: Tool) -> str:
    """Identity of everything the model is shown about a tool."""
    payload = json.dumps(
        [tool.name, tool.description or "", tool.parameters, tool.output_schema],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _fit_id(name: str) -> str:
    return f"fits::{name}"


class JevSearchTransform(BaseSearchTransform):
    """Search transform that ranks tools with TypeSafe's Jev.

    Experimental: the ranking parameters may change. Requires the ``jev``
    extra (``pip install "fastmcp[jev]"``) and a TypeSafe API key, read from
    ``TYPESAFE_API_KEY`` unless ``api_key`` or ``client`` is given. A missing
    key is an error at construction, not at the first search.

    Tool descriptions are model input. A description written to argue for
    its own selection can move the ranking; the transform only ranks tools
    the caller could already list, so that exposure is bounded by what the
    catalog holds.

    Args:
        model: The TypeSafe model name. ``jev-latest`` follows releases;
            pin a versioned id once you have tuned ``fit_threshold``.
        api_key: TypeSafe API key. Defaults to ``TYPESAFE_API_KEY``.
        client: A ready ``AsyncTypeSafeClient`` (or anything with an async
            ``system_one``) to use instead of building one.
        timeout: Seconds per API attempt when the transform builds its own
            client. A search is one to a few requests.
        shortlist: How many candidates each wide-pass request carries
            forward. The close read sees at most ``3 * shortlist``
            candidates; a larger catalog is narrowed with further wide
            passes first.
        fit_threshold: A candidate whose "does this tool do what the request
            asks" probability falls below this is dropped from the results.
            Tune it against queries from your own users.
        chunk_size: Tools per wide-pass request. Catalogs above this size
            are ranked in concurrent chunks. At most 255, the Choice limit.
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
        timeout: float = 10.0,
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
        if shortlist < 1:
            raise ValueError("shortlist must be at least 1")
        if not 1 <= chunk_size <= MAX_CHOICE_OPTIONS:
            raise ValueError(f"chunk_size must be between 1 and {MAX_CHOICE_OPTIONS}")
        if not 0 <= fit_threshold <= 1:
            raise ValueError("fit_threshold must be between 0 and 1")
        if summary_chars < 8 or detail_chars < 8:
            raise ValueError("summary_chars and detail_chars must be at least 8")
        if client is None:
            api_key = api_key or os.environ.get(API_KEY_ENV)
            if not api_key:
                raise ValueError(
                    f"JevSearchTransform needs a TypeSafe API key: pass api_key= "
                    f"or set {API_KEY_ENV}"
                )
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._client = client
        self._client_lock: asyncio.Lock | None = None
        self._shortlist = shortlist
        self._max_candidates = 3 * shortlist
        self._fit_threshold = fit_threshold
        self._chunk_size = chunk_size
        self._summary_chars = summary_chars
        self._detail_chars = detail_chars
        # rendered text per tool fingerprint; shared across searches but only
        # ever added to, so a concurrent search with a different catalog
        # cannot remove an entry another search is about to read
        self._texts: dict[str, tuple[str, str]] = {}

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    async def _get_client(self) -> SystemOneClient:
        if self._client is not None:
            return self._client
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()
        async with self._client_lock:
            if self._client is None:
                try:
                    from typesafe_sdk import AsyncTypeSafeClient
                except ImportError as e:
                    raise ImportError(
                        "JevSearchTransform needs the typesafe-sdk package: "
                        'install the jev extra with `pip install "fastmcp[jev]"`'
                    ) from e
                self._client = AsyncTypeSafeClient(
                    api_key=self._api_key, model=self._model, timeout=self._timeout
                )
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

    def _render(self, tools: Sequence[Tool]) -> tuple[dict[str, str], dict[str, str]]:
        """Summary and detail text for this search's catalog, as locals."""
        summaries: dict[str, str] = {}
        details: dict[str, str] = {}
        for tool in tools:
            key = _fingerprint(tool)
            if key not in self._texts:
                self._texts[key] = (
                    _summary(tool, self._summary_chars),
                    _detail(tool, self._detail_chars),
                )
            summaries[tool.name], details[tool.name] = self._texts[key]
        return summaries, details

    async def _rank_chunk(
        self, query: str, names: Sequence[str], summaries: Mapping[str, str]
    ) -> list[str]:
        """One wide request: rank every tool in a chunk by summary."""
        client = await self._get_client()
        response = await client.system_one(
            state={"request": query},
            questions={
                "which": {
                    "type": "choice",
                    "instructions": WIDE_INSTRUCTIONS,
                    "criteria": {name: summaries[name] for name in names},
                }
            },
        )
        probabilities: Mapping[str, float] = response.answers["which"].probabilities
        ranked = sorted(names, key=lambda name: -probabilities.get(name, 0.0))
        return ranked[: self._shortlist]

    async def _narrow(
        self, query: str, names: list[str], summaries: Mapping[str, str]
    ) -> list[str]:
        """Wide passes until the candidate set fits the close read.

        Probabilities from different chunks are not comparable, so each
        chunk's shortlist goes forward whole rather than through a merged
        cut. When the union is still too large for one close read, it is
        ranked again in chunks, which converges because every round keeps
        at most ``shortlist`` names per ``chunk_size``.
        """
        while len(names) > self._max_candidates:
            chunks = [
                names[i : i + self._chunk_size]
                for i in range(0, len(names), self._chunk_size)
            ]
            tasks = [
                asyncio.ensure_future(self._rank_chunk(query, chunk, summaries))
                for chunk in chunks
            ]
            try:
                ranked_chunks = await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                raise
            names = [name for ranked in ranked_chunks for name in ranked]
        return names

    async def _rerank(
        self,
        query: str,
        names: Sequence[str],
        summaries: Mapping[str, str],
        details: Mapping[str, str],
    ) -> list[str]:
        """One close-read request over the shortlist: which fits best, and
        whether each fits at all. Returns the names that fit, best first."""
        questions: dict[str, Any] = {
            "which": {
                "type": "choice",
                "instructions": RERANK_INSTRUCTIONS,
                "criteria": {name: details[name] for name in names},
            }
        }
        for name in names:
            questions[_fit_id(name)] = {
                "type": "noul",
                "instructions": (
                    f"Does the tool described at `tools.{name}` do the specific "
                    "thing the user's request in `request` asks for?"
                ),
            }
        client = await self._get_client()
        response = await client.system_one(
            state={
                "request": query,
                "tools": {name: summaries[name] for name in names},
            },
            questions=questions,
        )
        answers = response.answers
        probabilities: Mapping[str, float] = answers["which"].probabilities
        fitting = [
            name for name in names if answers[_fit_id(name)].noul >= self._fit_threshold
        ]
        return sorted(fitting, key=lambda name: -probabilities.get(name, 0.0))

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        if not tools or not query.strip():
            return []
        summaries, details = self._render(tools)
        by_name = {t.name: t for t in tools}
        candidates = await self._narrow(query, list(by_name), summaries)
        fitting = await self._rerank(query, candidates, summaries, details)
        return [by_name[name] for name in fitting[: self._max_results]]
