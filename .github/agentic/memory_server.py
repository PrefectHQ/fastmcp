"""MCP tools over the triage memory index, and the work claims every agent
working on fastmcp uses so they don't step on each other.

The assess agent calls `search` as often as it likes and `read` to open a hit,
the way diderot's agents use retrieve and expand. The turbopuffer key stays on
this server. It runs on Horizon behind gateway auth, so callers present a
Horizon service-account key and the server itself has no auth of its own.

Environment:
    TURBOPUFFER_API_KEY   turbopuffer key (server side only)
    WORK_ATPROTO_PASSWORD  app password for prefect.io, where claims are kept
                          (see flows/agentic_work.py); without it the claim
                          tools answer that claims are off
    GITHUB_TOKEN          optional, raises GitHub's rate limit for `read`

Run locally with `uv run fastmcp run memory_server.py --transport http`.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastmcp import FastMCP
from fastmcp.experimental.events import Event, EventBatch, EventPoll, EventsExtension
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flows.agentic_memory import (  # noqa: E402
    REPOSITORY,
    Index,
    MemoryConfig,
    best_per_item,
    is_bot,
    render,
    status_of,
    url_of,
)
from flows.agentic_work import (  # noqa: E402
    REASONS,
    TARGET_PATTERN,
    Agent,
    Board,
    Outcome,
    Phase,
    Reason,
    Repo,
)

SNIPPET = 600
PAGE = 100
MAX_PAGES = 20  # 2,000 comments, far beyond any fastmcp thread


mcp = FastMCP(
    "fastmcp-triage-memory",
    instructions=(
        "Search fastmcp's past issues, PRs and human comments. Hits are leads, not "
        "findings: the nearest items always come back however weak, so read an item "
        "before calling anything a duplicate, already fixed or already decided."
    ),
)

_index: Index | None = None


def index() -> Index:
    global _index
    if _index is None:
        _index = Index(MemoryConfig(mode="on"), os.environ["TURBOPUFFER_API_KEY"])
    return _index


@mcp.tool(annotations={"readOnlyHint": True})
def search(
    query: Annotated[
        str,
        Field(
            description="Words to look for: an error message, symptom, API name or title."
        ),
    ],
    kind: Annotated[
        Literal["issue", "pr"], Field(description="Search issues or PRs.")
    ] = "issue",
    exclude: Annotated[
        list[int], Field(description="Issue or PR numbers to leave out.")
    ] = [],  # noqa: B006
    limit: Annotated[int, Field(ge=1, le=10)] = 5,
) -> list[dict]:
    """Nearest past fastmcp issues or PRs to `query`, best first, one hit per item.

    Hybrid search (keywords and meaning) over titles, bodies and human comments.
    States are as of the last index sync, at most about 30 minutes old.
    """
    rows = index().search(query[:3000], kind, set(exclude), 40)
    return [
        {
            "number": row["number"],
            "title": row.get("title", ""),
            "state": row.get("state", ""),
            "url": url_of(row["number"], kind),
            "snippet": (row.get("text") or "")[:SNIPPET],
        }
        for row in best_per_item(rows, limit)
    ]


@mcp.tool(annotations={"readOnlyHint": True})
def read(
    number: Annotated[int, Field(description="Issue or PR number.", ge=1)],
) -> dict:
    """The current issue or PR, read live from GitHub: state, labels, body and
    human comments."""
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    base = f"https://api.github.com/repos/{REPOSITORY}/issues/{number}"
    with httpx.Client(headers=headers, timeout=20) as client:
        item = client.get(base)
        item.raise_for_status()
        data = item.json()
        comments: list[dict] = []
        for page in range(1, MAX_PAGES + 1) if data.get("comments") else ():
            response = client.get(f"{base}/comments?per_page={PAGE}&page={page}")
            response.raise_for_status()
            batch = response.json()
            comments.extend(c for c in batch if not is_bot(c.get("user")))
            if len(batch) < PAGE:
                break
    kind = "pr" if data.get("pull_request") else "issue"
    return {
        "number": number,
        "kind": kind,
        "state": status_of(data),
        "url": url_of(number, kind),
        "text": render(data, comments),
    }


# ---------- work claims: who is working on what ----------

_board: Board | None = None
_session: dict[str, str] = {}  # the PDS session, kept in memory on this server
_items: dict[int, tuple[float, dict | None]] = {}  # GitHub lookups, by expiry


def board() -> Board | None:
    global _board
    if not os.environ.get("WORK_ATPROTO_PASSWORD"):
        return None
    if _board is None:
        _board = Board(
            Repo(
                password=lambda: os.environ.get("WORK_ATPROTO_PASSWORD"),
                read_session=lambda: _session.get("value"),
                write_session=lambda value: _session.__setitem__("value", value),
            )
        )
    return _board


def _github_item(number: int) -> dict | None:
    """The public issue or PR, or None if PrefectHQ/fastmcp has no such item."""
    expiry, item = _items.get(number, (0.0, None))
    if expiry > time.time():
        return item
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    response = httpx.get(
        f"https://api.github.com/repos/{REPOSITORY}/issues/{number}",
        headers=headers,
        timeout=10,
    )
    item = response.json() if response.status_code == 200 else None
    _items[number] = (time.time() + (3600 if item else 60), item)
    return item


def _public_target(target: str) -> dict | None:
    """Only public PrefectHQ/fastmcp items (or a fixed deploy name) can be claimed.

    Returns the GitHub item for issue and PR targets, `{}` for deploys, and
    None when the target isn't something we'll put on a public record.
    """
    kind, _, value = target.partition(":")
    if kind == "deploy":
        return {}
    item = _github_item(int(value))
    if item is None or ("pull_request" in item) != (kind == "pr"):
        return None
    return item


def _context(target: str, item: dict, work: Board) -> dict:
    """What an agent should know before starting: where triage left this item,
    nearby past work from memory, and earlier attempts on it."""
    context: dict = {"history": work.history(target)}
    if not item:
        return context
    context["state"] = status_of(item)
    context["labels"] = sorted(label["name"] for label in item.get("labels", []))
    kind = "pr" if "pull_request" in item else "issue"
    try:
        hits = index().search(
            f"{item.get('title', '')}\n{(item.get('body') or '')[:2000]}",
            kind,
            {item["number"]},
            40,
        )
        context["related"] = [
            {
                "number": row["number"],
                "title": row.get("title", ""),
                "state": row.get("state", ""),
            }
            for row in best_per_item(hits, 5)
        ]
    except (KeyError, RuntimeError, ValueError, OSError, httpx.HTTPError):
        context["related"] = []
    return context


Target = Annotated[
    str,
    Field(
        description=(
            "What you're working on: issue:<number> or pr:<number> on "
            "PrefectHQ/fastmcp, or deploy:triage-stack / deploy:memory-server."
        ),
        pattern=TARGET_PATTERN,
    ),
]
Worker = Annotated[
    str,
    Field(
        description=(
            "Any id that stays the same for this whole piece of work, such as your "
            "session id. Only a hash of it is published."
        ),
        min_length=1,
        max_length=200,
    ),
]
Fence = Annotated[int, Field(description="The fence claim_work gave you.", ge=1)]
OFF = {"ok": False, "reason": "claims aren't configured on this server"}


@mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
def claim_work(target: Target, worker: Worker, agent: Agent = "agent") -> dict:
    """Claim an issue, PR or deploy before you start on it, so no one else does.

    If someone holds it, you get who and since when: don't start competing work.
    If you get it, you get a fence for report_progress and release_work, a
    30-minute lease that every report_progress renews, and context: the item's
    triage labels, related past items from triage memory, and earlier attempts.

    Claims are public records (sites.wisp.place/prefect.io/triage-floor shows
    them), carrying only the target, phase, agent kind and outcome. Never claim
    security work.
    """
    work = board()
    if work is None:
        return {"claimed": False, "reason": OFF["reason"]}
    item = _public_target(target)
    if item is None:
        return {
            "claimed": False,
            "reason": "not a public PrefectHQ/fastmcp issue or PR",
        }
    result = work.claim(target, worker, agent)
    if result["claimed"]:
        result["context"] = _context(target, item, work)
    return result


@mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
def report_progress(
    target: Target,
    worker: Worker,
    fence: Fence,
    phase: Annotated[
        Phase,
        Field(
            description=(
                "What you're doing now. pushing means opening or updating a PR; "
                "waiting means waiting on a person."
            )
        ),
    ],
    pr: Annotated[
        int | None, Field(description="Your PR's number, once it exists.", ge=1)
    ] = None,
) -> dict:
    """Report your phase on work you've claimed. Call it whenever your phase
    changes and at least every 30 minutes, or the claim lapses and counts as a
    failed attempt."""
    work = board()
    if work is None:
        return OFF
    if pr is not None and _public_target(f"pr:{pr}") is None:
        return {"ok": False, "reason": f"#{pr} isn't a public PrefectHQ/fastmcp PR"}
    return work.progress(target, worker, fence, phase, pr)


@mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
def release_work(
    target: Target,
    worker: Worker,
    fence: Fence,
    outcome: Annotated[
        Outcome,
        Field(
            description=(
                "pr: you opened or updated a PR (pass pr). handed_back: you're "
                "stopping and someone else should pick it up (pass reason). "
                "abandoned: you stopped without a useful result."
            )
        ),
    ],
    reason: Annotated[
        Reason | None,
        Field(description=f"Why you handed it back: one of {', '.join(REASONS)}."),
    ] = None,
    pr: Annotated[int | None, Field(description="The PR's number.", ge=1)] = None,
    used_related: Annotated[
        list[int],
        Field(
            description=(
                "Numbers of the related items from claim_work's context that "
                "actually helped. It tells triage which of its memory gets used."
            ),
            max_length=20,
        ),
    ] = [],  # noqa: B006
) -> dict:
    """Release a claim when you stop, whatever the reason. Every claim ends with
    an outcome; a claim left to lapse counts as a failed attempt."""
    work = board()
    if work is None:
        return OFF
    if pr is not None and _public_target(f"pr:{pr}") is None:
        return {"ok": False, "reason": f"#{pr} isn't a public PrefectHQ/fastmcp PR"}
    try:
        return work.release(target, worker, fence, outcome, reason, pr, used_related)
    except ValueError as error:
        return {"ok": False, "reason": str(error)}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
def active_work() -> list[dict]:
    """Everything someone has claimed right now: target, agent kind, phase, and
    since when. Clients that can't subscribe to the work.activity event can
    check this instead."""
    work = board()
    return [claim.public() for claim in work.claims()] if work else []


ActivityKind = Literal["claimed", "progress", "released", "expired"]

events = EventsExtension(poll_interval_ms=10_000)
mcp.add_extension(events)


@events.event("work.activity")
class WorkActivity(BaseModel):
    """Someone claimed, reported progress on, released, or let lapse a claim
    on fastmcp work. Events are notices, not authority: call claim_work
    before acting on one."""

    kind: ActivityKind
    target: str
    createdAt: str
    holder: str | None = None
    agent: str | None = None
    phase: str | None = None
    fence: int | None = None
    pr: int | None = None
    outcome: str | None = None
    reason: str | None = None
    usedRelated: list[int] | None = None

    def for_subscription(
        self,
        target: Annotated[str | None, Field(pattern=TARGET_PATTERN)] = None,
        kinds: list[ActivityKind] | None = None,
    ) -> WorkActivity | None:
        if target is not None and self.target != target:
            return None
        if kinds is not None and self.kind not in kinds:
            return None
        return self


@events.reader(WorkActivity)
def work_activity(poll: EventPoll) -> EventBatch:
    work = board()
    if work is None:
        return EventBatch(cursor=poll.cursor or "")
    limit = min(poll.max_events or 100, 100)
    entries, cursor = work.activity(poll.cursor, limit)
    return EventBatch(
        events=[
            Event(
                data=entry,
                event_id=entry["id"],
                timestamp=datetime.fromisoformat(entry["createdAt"]),
            )
            for entry in entries
        ],
        cursor=cursor,
        # the log page held nothing older than the cursor: some may be missing
        truncated=len(entries) >= limit,
    )


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
