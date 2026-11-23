"""Searchable memory of fastmcp's issues, PRs and comments, for triage.

Triage today only sees PRs that cite the issue number. This index lets it see
the closed issue that reported the same bug in other words, the PR that already
fixed it, or the discussion where a maintainer decided it. Each assessment gets
the closest issues and PRs as `related` evidence: leads to read, not verdicts.

The design borrows from diderot (bluesky-social/diderot):

- One document per item, rendered from its current state (title with number,
  state, labels, body, human comments). Chunks of ~1000 characters with a short
  prefix naming the item, so a slice from deep in a thread still says where it
  came from.
- Bot comments stay out, so triage never finds its own reviews or notices.
- Issues and PRs are ranked separately with one hit per item, so the larger set
  can't crowd the other out.
- The item under triage is excluded from its own search.

Unlike diderot, search is hybrid: turbopuffer embeds the text itself and fuses
vector and BM25 results with RRF, so exact error strings still match. On 60
past duplicates that can be found, the canonical item is in the top five for 80%
with a cheap reranker on top (70% without it, 52% with keyword search alone).

`triage-memory-sync` keeps the index current from GitHub's updated-since
listing. `fastmcp_triage_config.memory` turns it all on or off:
`{"memory": {"mode": "on"}}`. The default is off.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPOSITORY = "PrefectHQ/fastmcp"
SECRET_BLOCK = "turbopuffer-api-key"

SINGLE_CHUNK = 1500
WINDOW = 1000
OVERLAP = 200
LOOKBACK = 64
MAX_COMMENT = 4000
WRITE_BATCH = 256  # turbopuffer embeds at most 256 documents per write
OVERFETCH = 40


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    mode: Literal["off", "on"] = "off"
    namespace: str = "fastmcp-triage-memory-v1"
    region: str = "aws-us-east-1"
    embed_model: str = "openai/text-embedding-3-small"
    # Reorders the fused candidates; None keeps plain RRF. On past duplicates it
    # took top-five recall from 70% to 80% and the #1 hit from 34% to 53%.
    rerank_model: str | None = "voyage/rerank-3-lite"
    top_k: int = Field(default=5, ge=1, le=10)


def memory_config(raw: object) -> MemoryConfig:
    value = raw.get("memory") if isinstance(raw, dict) else None
    try:
        return MemoryConfig.model_validate(value or {})
    except ValidationError:
        return MemoryConfig()


def is_bot(user: dict | None) -> bool:
    user = user or {}
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def kind_of(item: dict) -> str:
    return "pr" if item.get("pull_request") else "issue"


def status_of(item: dict) -> str:
    if (item.get("pull_request") or {}).get("merged_at"):
        return "merged"
    if item.get("state") == "closed" and item.get("state_reason"):
        return f"closed ({item['state_reason']})"
    return str(item.get("state", ""))


def prefix(item: dict) -> str:
    return (
        f"fastmcp #{item['number']} ({kind_of(item)}, {status_of(item)}): "
        f"{item.get('title') or ''}"
    )[:250]


def render(item: dict, comments: Iterable[dict]) -> str:
    labels = [label["name"] for label in item.get("labels") or []]
    head = [
        f"#{item['number']}: {item.get('title') or ''}",
        f"Kind: {kind_of(item)}. State: {status_of(item)}. "
        f"Author: {(item.get('user') or {}).get('login', '')}.",
    ]
    if labels:
        head.append("Labels: " + ", ".join(labels))
    parts = ["\n".join(head)]
    if (item.get("body") or "").strip():
        parts.append(item["body"].strip())
    for comment in comments:
        text = (comment.get("body") or "").strip()
        if not text or is_bot(comment.get("user")):
            continue
        if len(text) > MAX_COMMENT:
            text = text[:MAX_COMMENT] + " [comment cut]"
        login = (comment.get("user") or {}).get("login", "")
        parts.append(f"**{login}** ({comment['created_at'][:10]}): {text}")
    return "\n\n".join(parts)


def windows(text: str) -> list[str]:
    """One chunk up to 1500 characters; otherwise ~1000-character windows with
    200 of overlap, cut at whitespace when there is some nearby."""
    if len(text) <= SINGLE_CHUNK:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + WINDOW, len(text))
        if end < len(text):
            cut = max(
                text.rfind(" ", end - LOOKBACK, end),
                text.rfind("\n", end - LOOKBACK, end),
            )
            if cut > start:
                end = cut
        chunks.append(text[start:end])
        if end >= len(text):
            return chunks
        start = max(end - OVERLAP, start + 1)
    return chunks


def documents(item: dict, comments: Iterable[dict]) -> list[dict]:
    body = render(item, comments)
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    head = prefix(item)
    return [
        {
            "id": f"{item['number']}:{index}",
            "text": f"{head}\n\n{chunk}",
            "number": item["number"],
            "kind": kind_of(item),
            "state": status_of(item),
            "title": item.get("title") or "",
            "updated_at": item["updated_at"],
            "content_hash": digest,
        }
        for index, chunk in enumerate(windows(body))
    ]


def best_per_item(rows: Iterable[dict], top_k: int) -> list[dict]:
    """Keep each item's best-ranked chunk, in rank order, up to `top_k` items."""
    seen: dict[int, dict] = {}
    for row in rows:
        if row["number"] not in seen:
            seen[row["number"]] = row
        if len(seen) >= top_k:
            break
    return list(seen.values())


def url_of(number: int, kind: str) -> str:
    return f"https://github.com/{REPOSITORY}/{'pull' if kind == 'pr' else 'issues'}/{number}"


Search = Callable[[str, str, set[int], int], list[dict]]


def related(search: Search, issue: dict, top_k: int) -> dict:
    """The closest issues and PRs to `issue`, ranked per kind, itself excluded."""
    query = f"{issue.get('title') or ''}\n\n{issue.get('body') or ''}"[:3000]
    exclude = {issue["number"]}
    result: dict[str, Any] = {}
    for kind in ("issue", "pr"):
        rows = best_per_item(search(query, kind, exclude, OVERFETCH), top_k)
        result[kind + "s"] = [
            {
                "number": row["number"],
                "title": row.get("title", ""),
                "state": row.get("state", ""),
                "url": url_of(row["number"], kind),
            }
            for row in rows
        ]
    result["policy"] = (
        "Nearest past issues and PRs by hybrid search over titles, bodies and human "
        "comments, best first. The nearest items always come back however weak, so "
        "these are leads, not findings: read an item before calling this a duplicate, "
        "already fixed or already decided. States are as of the last index sync."
    )
    return result


class Index:
    """A turbopuffer namespace holding the chunks."""

    def __init__(self, config: MemoryConfig, api_key: str) -> None:
        import turbopuffer

        self.config = config
        client = turbopuffer.Turbopuffer(api_key=api_key, region=config.region)
        self.namespace = client.namespace(config.namespace)

    def schema(self) -> dict:
        return {
            "text": {
                "type": "string",
                "full_text_search": {"language": "english", "stemming": True},
                "embed": self.config.embed_model,
            },
            "number": {"type": "int", "filterable": True},
            "kind": {"type": "string", "filterable": True},
            "state": {"type": "string", "filterable": True},
            "title": {"type": "string", "filterable": False},
            "updated_at": {"type": "datetime", "filterable": True},
            "content_hash": {"type": "string", "filterable": False},
        }

    def replace(self, docs: list[dict]) -> None:
        """Replace every chunk of the items in `docs` (an edit can shrink an item)."""
        from turbopuffer import omit

        numbers = sorted({doc["number"] for doc in docs})
        schema: Any = self.schema()
        for start in range(0, len(docs), WRITE_BATCH):
            rows: Any = docs[start : start + WRITE_BATCH]
            # A write applies delete_by_filter before its upserts, so the first
            # slice clears the old chunks and later slices only add.
            self.namespace.write(
                delete_by_filter=("number", "In", numbers) if start == 0 else omit,
                upsert_rows=rows,
                schema=schema,
                distance_metric="cosine_distance",
            )

    def search(self, text: str, kind: str, exclude: set[int], limit: int) -> list[dict]:
        filters: Any = ("kind", "Eq", kind)
        if exclude:
            not_in = "NotIn"  # codespell:ignore
            filters = ("And", [filters, ("number", not_in, sorted(exclude))])

        model = self.config.rerank_model
        attributes = ["number", "title", "state"] + (["text"] if model else [])

        def sub(rank_by: Any) -> Any:
            return {
                "rank_by": rank_by,
                "limit": limit,
                "filters": filters,
                "include_attributes": attributes,
            }

        rerank: Any = (
            {"model": model, "attribute": "text", "query": text[:2000]}
            if model
            else ("RRF",)
        )
        response = self.namespace.multi_query(
            queries=[
                sub(("text", "ANN", ("Embed", text))),
                sub(("text", "BM25", text[:2000])),
            ],
            rerank_by=rerank,
            limit=limit,
        )
        return [row.to_dict() for row in response.results[0].rows or []]


def changed_since(pages: Callable[[str], list[dict]], since: datetime) -> list[dict]:
    stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return pages(
        f"repos/{REPOSITORY}/issues?state=all&sort=updated&direction=asc&since={stamp}"
    )


def sync(
    items: list[dict],
    comments_of: Callable[[int], list[dict]],
    replace: Callable[[list[dict]], None],
    batch_items: int = 50,
) -> int:
    """Re-render and write each item. Returns the number of items written."""
    batch: list[dict] = []
    written = 0
    for item in items:
        thread = comments_of(item["number"]) if item.get("comments") else []
        batch.extend(documents(item, thread))
        written += 1
        if written % batch_items == 0:
            replace(batch)
            batch = []
    if batch:
        replace(batch)
    return written


def lookback(hours: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(hours=hours)
