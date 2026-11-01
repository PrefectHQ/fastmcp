"""Publish public backlog history without inferring closures from count deltas.

The previous backlog.json retains observed samples imported from the existing
half-hourly collector. Older samples are explicitly reconstructed. GitHub's
current records give creation and latest closure dates, not every reopen cycle.
"""

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = "PrefectHQ/fastmcp"
SCHEMA = "fastmcp-backlog/1"
CENTRAL = ZoneInfo("America/Chicago")


def fetch_items() -> list[dict]:
    result = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{REPO}/issues?state=all&per_page=100&sort=created&direction=asc",
            "--jq",
            ".[] | {number, title, state, state_reason, created_at, closed_at, draft, pull_request: (if .pull_request then {merged_at: .pull_request.merged_at} else null end)}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def newest_number() -> int:
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{REPO}/issues?state=all&per_page=1&sort=created&direction=desc",
            "--jq",
            ".[0].number",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout)


def require_complete(items: list[dict], newest: int) -> None:
    if newest not in {item["number"] for item in items}:
        raise RuntimeError(
            f"Collected {len(items)} items without #{newest}; pagination ended early"
        )


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def kind(item: dict) -> str:
    return "prs" if item.get("pull_request") is not None else "issues"


def outcome(item: dict) -> str | None:
    if item["state"] != "closed":
        return None
    if kind(item) == "prs":
        return "merged" if item["pull_request"].get("merged_at") else "closed_unmerged"
    return {"completed": "completed", "not_planned": "not_planned"}.get(
        item.get("state_reason"), "other_closed"
    )


def sample(items: list[dict], at: datetime, source: str) -> dict:
    row = {"at": at.isoformat(), "source": source}
    for category in ("issues", "prs"):
        eligible = [
            item
            for item in items
            if kind(item) == category
            and parse_time(item["created_at"]) <= at
            and (not item.get("closed_at") or parse_time(item["closed_at"]) > at)
        ]
        row[category] = {"open": len(eligible)}
        if source == "live" and category == "prs":
            row[category]["draft"] = sum(bool(item.get("draft")) for item in eligible)
    return row


def build(items: list[dict], previous: dict, now: datetime) -> dict:
    samples = list(previous.get("samples", []))
    if not samples:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        samples = [
            sample(items, midnight - timedelta(days=d), "backfill")
            for d in range(365, 0, -1)
        ]
    samples.append(sample(items, now, "live"))
    samples = sorted(
        {row["at"]: row for row in samples}.values(),
        key=lambda row: parse_time(row["at"]),
    )
    today = now.astimezone(CENTRAL).date()
    daily = {}
    for offset in range(365, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        daily[day] = {
            "day": day,
            "issues_opened": 0,
            "prs_opened": 0,
            "completed": 0,
            "not_planned": 0,
            "other_closed": 0,
            "merged": 0,
            "closed_unmerged": 0,
        }
    events = []
    for item in items:
        status = outcome(item)
        for field, event in (
            ("created_at", f"{kind(item)}_opened"),
            ("closed_at", status),
        ):
            if not event or not item.get(field):
                continue
            at = item[field]
            day = parse_time(at).astimezone(CENTRAL).date().isoformat()
            if day in daily:
                daily[day][event] += 1
                if day >= (today - timedelta(days=30)).isoformat():
                    events.append(
                        {
                            "number": item["number"],
                            "title": item["title"],
                            "kind": kind(item),
                            "event": event,
                            "at": at,
                            "day": day,
                        }
                    )
    return {
        "schema": SCHEMA,
        "repo": REPO,
        "as_of": now.isoformat(),
        "timezone": "America/Chicago",
        "samples": samples,
        "daily": list(daily.values()),
        "events": sorted(events, key=lambda event: event["at"], reverse=True),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--import-history", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = json.loads(args.previous.read_text()) if args.previous else {}
    if args.previous and previous.get("schema") != SCHEMA:
        raise ValueError("Unexpected backlog history schema")
    if args.import_history:
        imported = [
            json.loads(line)
            for line in args.import_history.read_text().splitlines()
            if line.strip()
        ]
        previous["samples"] = previous.get("samples", []) + [
            {key: row[key] for key in ("at", "source", "issues", "prs")}
            for row in imported
            if row["repo"] == REPO
        ]
    # Timestamp the beginning of collection so updates during pagination are
    # not represented as a snapshot from after an item was read.
    now = datetime.now(timezone.utc).replace(microsecond=0)
    # Pages are oldest-first, so a listing that stops early silently drops the
    # newest items. Anything created before collection began must be present.
    newest = newest_number()
    items = fetch_items()
    require_complete(items, newest)
    doc = build(items, previous, now)
    args.output.write_text(json.dumps(doc, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
