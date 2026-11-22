"""MCP tools over the triage memory index, for the triage agents.

The assess agent calls `search` as often as it likes and `read` to open a hit,
the way diderot's agents use retrieve and expand. The turbopuffer key stays on
this server. It runs on Horizon behind gateway auth, so callers present a
Horizon service-account key and the server itself has no auth of its own.

Environment:
    TURBOPUFFER_API_KEY   turbopuffer key (server side only)
    GITHUB_TOKEN          optional, raises GitHub's rate limit for `read`

Run locally with `uv run fastmcp run memory_server.py --transport http`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastmcp import FastMCP
from pydantic import Field

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


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
