"""Backfill `io.prefect.fastmcp.floor.day` records from GitHub history.

The feed workflow writes one history record per UTC day from then on. This
script writes the days before that, so the floor's charts have a baseline from
the start. It rebuilds each day from issue and PR timestamps and from the issue
events API (gate label, draft changes), samples every two hours to count moves
between stages, and writes every finished day. Today is left to the workflow.

Backfilled days carry `"backfilled": true`. Their `stalePermille` is an
estimate: the events API does not include comments, so it counts an item as
untouched when only comments touched it.

Usage (needs gh, and the same ATPROTO_* environment as the feed):
    uv run scripts/floor_history.py --days 120 [--dry-run]
"""

import argparse
import json
import os
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from triage_floor_feed import (  # noqa: E402
    GATE_LABEL,
    NSID,
    REPO,
    STAGES,
    STALE_AFTER,
    gh,
    is_bot,
    parse_time,
    session,
    xrpc,
)

STEP = timedelta(hours=2)
QUIET_EVENTS = {"subscribed", "unsubscribed", "mentioned", "deployed"}


class Item:
    def __init__(self, raw: dict, events: list[dict], draft_now: bool) -> None:
        self.n = raw["number"]
        self.pr = "pull_request" in raw
        self.bot = is_bot(raw.get("user"))
        self.created = parse_time(raw["created_at"])
        self.closed = parse_time(raw["closed_at"]) if raw.get("closed_at") else None
        merged = (raw.get("pull_request") or {}).get("merged_at")
        self.merged = parse_time(merged) if merged else None
        events = sorted(events, key=lambda e: e["created_at"])
        self.gate = [
            (parse_time(e["created_at"]), e["event"] == "labeled")
            for e in events
            if e["event"] in ("labeled", "unlabeled")
            and (e.get("label") or {}).get("name") == GATE_LABEL
        ]
        self.gate_start = not self.gate and GATE_LABEL in {
            label["name"] for label in raw.get("labels", [])
        }
        self.drafts = [
            (parse_time(e["created_at"]), e["event"] == "convert_to_draft")
            for e in events
            if e["event"] in ("ready_for_review", "convert_to_draft")
        ]
        # the first draft event tells us what it was before; else use today's
        if self.drafts:
            self.draft_start = not self.drafts[0][1]
        else:
            self.draft_start = draft_now
        self.touched = [
            parse_time(e["created_at"])
            for e in events
            if e["event"] not in QUIET_EVENTS
        ]

    def open_at(self, at: datetime) -> bool:
        return self.created <= at and (self.closed is None or self.closed > at)

    def stage_at(self, at: datetime) -> str | None:
        if not self.open_at(at):
            return None
        if not self.pr:
            return "issues"
        if state(self.gate, self.gate_start, at):
            return "gate"
        if self.bot:
            return "bots"
        if state(self.drafts, self.draft_start, at):
            return "drafts"
        return "ready"

    def last_touched(self, at: datetime) -> datetime:
        return max([self.created, *(t for t in self.touched if t <= at)])


def state(changes: list[tuple[datetime, bool]], start: bool, at: datetime) -> bool:
    value = start
    for when, new in changes:
        if when > at:
            break
        value = new
    return value


def load(days: int) -> list[Item]:
    since = (datetime.now(UTC) - timedelta(days=days + 1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    raw = {
        i["number"]: i
        for path in (
            f"repos/{REPO}/issues?state=open&per_page=100",
            f"repos/{REPO}/issues?state=all&since={since}&per_page=100",
        )
        for i in gh(path, paginate=True, timeout=300)
    }
    drafts = {
        p["number"]
        for p in gh(f"repos/{REPO}/pulls?state=open&per_page=100", paginate=True)
        if p.get("draft")
    }
    events: dict[int, list[dict]] = {}
    # about 300 pages: the API keeps the latest 30,000 events
    for e in gh(f"repos/{REPO}/issues/events?per_page=100", paginate=True, timeout=900):
        number = (e.get("issue") or {}).get("number")
        if number in raw:
            events.setdefault(number, []).append(e)
    return [
        Item(r, events.get(n, []), n in drafts and not r.get("closed_at"))
        for n, r in raw.items()
    ]


def records(items: list[Item], days: int, now: datetime) -> list[dict]:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - timedelta(days=days)
    out = []
    at = first
    before = {i.n: i.stage_at(at) for i in items}
    for d in range(days):
        start, end = first + timedelta(days=d), first + timedelta(days=d + 1)
        moved = {name: {"in": 0, "out": 0} for name in STAGES}
        while at < end:
            at += STEP
            for i in items:
                s = i.stage_at(at)
                if s != before[i.n]:
                    if s in moved:
                        moved[s]["in"] += 1
                    if before[i.n] in moved:
                        moved[before[i.n]]["out"] += 1
                    before[i.n] = s
        moment = end - timedelta(seconds=1)
        open_items = [i for i in items if i.open_at(moment)]
        stages = {i.n: i.stage_at(moment) for i in open_items}
        ages = sorted(
            (moment - i.created).days for i in open_items if stages[i.n] == "ready"
        )
        stale = sum(
            1 for i in open_items if moment - i.last_touched(moment) > STALE_AFTER
        )
        count = lambda name: sum(1 for s in stages.values() if s == name)  # noqa: E731
        within = lambda t: t is not None and start <= t < end  # noqa: E731
        out.append(
            {
                "$type": f"{NSID}.day",
                "day": start.strftime("%Y-%m-%d"),
                "issues": count("issues"),
                "ready": count("ready"),
                "gated": count("gate"),
                "drafts": count("drafts"),
                "bots": count("bots"),
                "readyWaitDays": round(statistics.median(ages)) if ages else 0,
                "stalePermille": round(1000 * stale / len(open_items))
                if open_items
                else 0,
                "opened": sum(1 for i in items if within(i.created)),
                "closed": sum(1 for i in items if within(i.closed)),
                "merged": sum(1 for i in items if within(i.merged)),
                "flow": moved,
                "backfilled": True,
                "updatedAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--session-file")
    args = parser.parse_args()
    now = datetime.now(UTC)
    days = records(load(args.days), args.days, now)
    if args.dry_run:
        for day in days:
            print(json.dumps({k: v for k, v in day.items() if k != "$type"}))
        return 0
    pds = os.environ["ATPROTO_PDS"]
    did, token = session(pds, args.session_file, now)
    for day in days:
        body = {
            "repo": did,
            "collection": f"{NSID}.day",
            "rkey": day["day"],
            "record": day,
            "validate": False,
        }
        xrpc(pds, "com.atproto.repo.putRecord", body, token)
    print(f"wrote {len(days)} days, {days[0]['day']} to {days[-1]['day']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
