"""Publish fastmcp triage activity as atproto records for the live triage floor.

`to_event` turns one GitHub event into one small public record; the webhook
receiver in scripts/floor_receiver writes those as events happen. The workflow
that runs this script rewrites a snapshot of every open issue and PR whenever
something can change what is open. Viewers of the floor subscribe to these
records over Jetstream, so they get each change pushed to them without calling
GitHub themselves.

Only public data goes out: titles, numbers, labels, logins and timestamps that
GitHub already shows to anyone. Pushes and branch activity are not published;
CI runs on main are (`to_ci`), as are the triage flows' run states (`to_run`).
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

REPO = "PrefectHQ/fastmcp"
NSID = "io.prefect.fastmcp.floor"
GATE_LABEL = "missing-issue-link"
SNAPSHOT_EVENTS = {
    "issues",
    "pull_request",
    "pull_request_target",
    "schedule",
    "workflow_dispatch",
}


def is_bot(user: dict | None) -> bool:
    user = user or {}
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def excerpt(body: str | None) -> str | None:
    """The start of a comment, on one line, for the floor's log."""
    text = " ".join((body or "").split())
    if not text:
        return None
    return text if len(text) <= 160 else text[:159].rstrip() + "…"


def to_event(name: str, payload: dict, now: datetime) -> dict | None:
    """One GitHub webhook event -> one floor event record, or None to skip it."""
    action = payload.get("action", "")
    sender = payload.get("sender") or {}
    thing = payload.get("pull_request") or payload.get("issue") or {}
    base = {
        "$type": f"{NSID}.event",
        "number": thing.get("number"),
        "title": (thing.get("title") or "")[:300],
        "pr": bool(payload.get("pull_request") or thing.get("pull_request")),
        "actor": sender.get("login", ""),
        "bot": is_bot(sender),
        "url": thing.get("html_url", ""),
        "createdAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    kind = None
    if name in {"issues", "pull_request", "pull_request_target"}:
        if action == "closed":
            kind = "merged" if thing.get("merged") else "closed"
        elif action in {"opened", "reopened"}:
            kind = "opened"
        elif action == "labeled":
            label = (payload.get("label") or {}).get("name", "")
            kind = "gated" if label == GATE_LABEL else "labeled"
            base["label"] = label
        elif action == "ready_for_review":
            kind = "updated"
        elif action == "synchronize":
            kind = "pushed"
    elif name == "issue_comment" and action == "created":
        kind = "botcomment" if base["bot"] else "comment"
        base["excerpt"] = excerpt((payload.get("comment") or {}).get("body"))
        base["author"] = (thing.get("user") or {}).get("login", "")
    elif name in {"pull_request_review", "pull_request_review_comment"}:
        kind = "botcomment" if base["bot"] else "review"
        base["pr"] = True
        review = payload.get("review") or payload.get("comment") or {}
        base["excerpt"] = excerpt(review.get("body"))
        base["author"] = (thing.get("user") or {}).get("login", "")
        if review.get("state"):
            base["state"] = review["state"].lower()
    elif name == "release" and action == "published":
        release = payload.get("release") or {}
        kind = "release"
        base.update(
            number=None,
            title=release.get("tag_name", "release"),
            url=release.get("html_url", ""),
        )
    if not kind:
        return None
    base["kind"] = kind
    return {k: v for k, v in base.items() if v is not None}


# GitHub Actions workflows that run on pushes to main: the floor's CI. One
# record per run, rewritten as the run moves from queued to done.
CI_EVENTS = {"push"}


def stamp(value: str | None) -> str | None:
    """A GitHub or Prefect timestamp as the floor's UTC ISO form, or None."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def to_ci(payload: dict) -> tuple[str, dict] | None:
    """A workflow_run webhook -> (rkey, floor.ci record), for runs on main only."""
    run = payload.get("workflow_run") or {}
    if run.get("head_branch") != "main" or run.get("event") not in CI_EVENTS:
        return None
    if not isinstance(run.get("id"), int) or run.get("status") is None:
        return None
    record = {
        "$type": f"{NSID}.ci",
        "workflow": (run.get("name") or "")[:100],
        "status": run["status"],
        "conclusion": run.get("conclusion"),
        "sha": (run.get("head_sha") or "")[:7],
        "title": (run.get("display_title") or "").split("\n")[0][:200],
        "url": run.get("html_url", ""),
        "startedAt": stamp(run.get("run_started_at")),
        # when GitHub last changed the run; an older delivery never overwrites a newer one
        "at": stamp(run.get("updated_at")),
    }
    # zero-padded run ids sort the same as numbers, so listRecords gives newest first
    return f"{run['id']:016d}", {k: v for k, v in record.items() if v is not None}


# Prefect flows the floor shows, by flow name. Anything else is ignored.
FLOWS = {
    "fastmcp-stack-batch",
    "fastmcp-triage-intake",
    "fastmcp-triage-verify",
    "fastmcp-triage-finalize",
    "fastmcp-verify-contribution",
    "fastmcp-triage-sweep",
}
NUMBER_KEYS = {"issues", "pull_requests", "issue", "pull_request", "number", "pr"}


def item_numbers(params: object, depth: int = 0) -> list[int]:
    """Issue and PR numbers a flow run was started for, from its parameters."""
    found: list[int] = []
    if depth > 3 or not isinstance(params, dict):
        return found
    for key, value in params.items():
        if key in NUMBER_KEYS:
            values = value if isinstance(value, list) else [value]
            found += [v for v in values if isinstance(v, int) and 0 < v < 10**6]
        elif isinstance(value, dict):
            found += item_numbers(value, depth + 1)
    return list(dict.fromkeys(found))[:10]


def to_run(body: dict) -> tuple[str, dict] | None:
    """A Prefect automation's flow-run state change -> (rkey, floor.run record).

    Only the flow's name, the state, times and issue/PR numbers go out: never
    the run's name, parameters, logs or Prefect URLs.
    """
    flow = body.get("flow")
    run_id = str(body.get("id") or "")
    state = str(body.get("event") or "").rsplit(".", 1)[-1]
    created = stamp(body.get("created"))
    at = stamp(body.get("occurred"))
    if flow not in FLOWS or not created or not at or not state.isalpha():
        return None
    if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
        return None
    record = {
        "$type": f"{NSID}.run",
        "flow": flow,
        "state": state.lower(),
        "numbers": item_numbers(body.get("params")),
        "createdAt": created,
        "startedAt": stamp(body.get("start")),
        "at": at,
    }
    # created time first, so listRecords gives the newest runs first
    rkey = created[:19].replace("-", "").replace(":", "") + "-" + run_id[:8]
    return rkey, {k: v for k, v in record.items() if v is not None}


def gh(path: str, *, paginate: bool = False, timeout: int = 60):
    command = ["gh", "api", path]
    if paginate:
        command += ["--paginate", "--slurp"]
    out = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=timeout
    ).stdout
    value = json.loads(out)
    return [x for page in value for x in page] if paginate else value


def slim(item: dict) -> dict:
    labels = [label["name"] for label in item.get("labels", [])]
    return {
        "n": item["number"],
        "t": (item.get("title") or "")[:200],
        "pr": "pull_request" in item,
        "draft": bool(item.get("draft")),
        "bot": is_bot(item.get("user")),
        "a": (item.get("user") or {}).get("login", ""),
        "labels": labels[:8],
        "created": item["created_at"],
        "updated": item["updated_at"],
    }


def snapshot(now: datetime) -> dict:
    items = gh(f"repos/{REPO}/issues?state=open&per_page=100", paginate=True)
    # the issues list leaves draft out; fill it in from the pulls list
    drafts = {
        p["number"]
        for p in gh(f"repos/{REPO}/pulls?state=open&per_page=100", paginate=True)
        if p.get("draft")
    }
    open_items = []
    for item in items:
        row = slim(item)
        row["draft"] = row["n"] in drafts
        open_items.append(row)
    since = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    def count(q: str, kinds: tuple[str, ...] = ("issue", "pr")) -> int:
        # the search API wants each kind asked for separately
        return sum(
            gh(f"search/issues?per_page=1&q=repo:{REPO}+is:{k}+{q}")["total_count"]
            for k in kinds
        )

    week = {
        "opened": count(f"created:>={since}"),
        "closed": count(f"is:closed+closed:>={since}"),
        "merged": count(f"is:merged+merged:>={since}", ("pr",)),
    }
    return {
        "$type": f"{NSID}.snapshot",
        "repo": REPO,
        "open": open_items,
        "week": week,
        "updatedAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }


STAGES = ("issues", "gate", "ready")
STALE_AFTER = timedelta(days=30)


def stage(row: dict) -> str:
    """The floor's bucket for one open item, in the order the site applies them."""
    if not row["pr"]:
        return "issues"
    if GATE_LABEL in row["labels"]:
        return "gate"
    if row["bot"]:
        return "bots"
    if row["draft"]:
        return "drafts"
    return "ready"


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def day_counts(open_items: list[dict], now: datetime) -> dict:
    """How many open items each stage holds, as the floor counts them."""
    counts = {"issues": 0, "gate": 0, "ready": 0, "drafts": 0, "bots": 0}
    for row in open_items:
        counts[stage(row)] += 1
    ages = sorted(
        (now - parse_time(row["created"])).days
        for row in open_items
        if stage(row) == "ready"
    )
    stale = sum(
        1 for row in open_items if now - parse_time(row["updated"]) > STALE_AFTER
    )
    return {
        "issues": counts["issues"],
        "ready": counts["ready"],
        "gated": counts["gate"],
        "drafts": counts["drafts"],
        "bots": counts["bots"],
        # records hold integers only, so the median and share are rounded
        "readyWaitDays": round(statistics.median(ages)) if ages else 0,
        "stalePermille": round(1000 * stale / len(open_items)) if open_items else 0,
    }


def flow(before: list[dict], after: list[dict]) -> dict:
    """Items that entered and left each stage between two snapshots."""
    was = {row["n"]: stage(row) for row in before}
    now = {row["n"]: stage(row) for row in after}
    out = {}
    for name in STAGES:
        old = {n for n, s in was.items() if s == name}
        new = {n for n, s in now.items() if s == name}
        out[name] = {"in": len(new - old), "out": len(old - new)}
    return out


def day_totals(day: str) -> dict:
    """Items opened, closed and merged on one UTC day."""

    def count(q: str, kinds: tuple[str, ...] = ("issue", "pr")) -> int:
        return sum(
            gh(f"search/issues?per_page=1&q=repo:{REPO}+is:{k}+{q}")["total_count"]
            for k in kinds
        )

    return {
        "opened": count(f"created:{day}"),
        "closed": count(f"is:closed+closed:{day}"),
        "merged": count(f"is:merged+merged:{day}", ("pr",)),
    }


def xrpc(pds: str, method: str, body: dict, token: str | None = None) -> dict:
    request = urllib.request.Request(
        f"{pds.rstrip('/')}/xrpc/{method}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        # never echo the response body: it can carry tokens
        raise RuntimeError(f"{method} failed with HTTP {exc.code}") from None


def xrpc_get(pds: str, method: str, params: dict) -> dict | None:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    try:
        with urllib.request.urlopen(
            f"{pds.rstrip('/')}/xrpc/{method}?{query}", timeout=30
        ) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 404):  # RecordNotFound
            return None
        raise RuntimeError(f"{method} failed with HTTP {exc.code}") from None


def put_snapshot(
    pds: str, did: str, token: str, snap: dict
) -> tuple[bool, dict | None]:
    """Write the snapshot unless a run that collected later already did.

    Runs overlap, so a run that read GitHub before a close could finish after
    the run that saw it. Compare-and-swap on the record's CID, and give way to
    any snapshot collected after ours. Returns whether it wrote, and the
    snapshot it replaced.
    """
    for _ in range(3):
        current = xrpc_get(
            pds,
            "com.atproto.repo.getRecord",
            {"repo": did, "collection": f"{NSID}.snapshot", "rkey": "self"},
        )
        previous = current["value"] if current else None
        if previous and previous.get("updatedAt", "") >= snap["updatedAt"]:
            return False, None
        body = {
            "repo": did,
            "collection": f"{NSID}.snapshot",
            "rkey": "self",
            "record": snap,
            "validate": False,
        }
        if current:
            body["swapRecord"] = current["cid"]
        try:
            xrpc(pds, "com.atproto.repo.putRecord", body, token)
            return True, previous
        except RuntimeError as exc:
            if "HTTP 400" not in str(exc):  # InvalidSwap: someone wrote first
                raise
    return False, None


def put_day(pds: str, did: str, token: str, record: dict, moved: dict | None) -> None:
    """Rewrite today's history record, adding this run's stage movements.

    Counts are replaced by the newest snapshot's; movements accumulate over the
    day.
    Compare-and-swap keeps two overlapping runs from losing each other's.
    """
    for _ in range(3):
        current = xrpc_get(
            pds,
            "com.atproto.repo.getRecord",
            {"repo": did, "collection": f"{NSID}.day", "rkey": record["day"]},
        )
        stored = (current or {}).get("value", {})
        old = stored.get("flow", {})
        totals = {
            name: {
                k: old.get(name, {}).get(k, 0) + (moved or {}).get(name, {}).get(k, 0)
                for k in ("in", "out")
            }
            for name in STAGES
        }
        body = {
            "repo": did,
            "collection": f"{NSID}.day",
            "rkey": record["day"],
            # an older run that finishes last keeps the newer counts and only
            # adds its movements
            "record": {
                **(
                    stored
                    if stored.get("updatedAt", "") > record.get("updatedAt", "")
                    else record
                ),
                "flow": totals,
            },
            "validate": False,
            "swapRecord": current["cid"] if current else None,
        }
        try:
            xrpc(pds, "com.atproto.repo.putRecord", body, token)
            return
        except RuntimeError as exc:
            if "HTTP 400" not in str(exc):
                raise


def day_record(snap: dict, now: datetime) -> dict:
    day = now.strftime("%Y-%m-%d")
    return {
        "$type": f"{NSID}.day",
        "day": day,
        **day_counts(snap["open"], now),
        **day_totals(day),
        "updatedAt": snap["updatedAt"],
    }


ACCESS_TTL = timedelta(minutes=90)  # bsky access tokens live about two hours


def session(pds: str, path: str | None, now: datetime) -> tuple[str, str]:
    """Reuse a cached session so most runs never call createSession.

    bsky-hosted PDSes allow 300 createSession calls a day per account; fastmcp
    can see more events than that. Access tokens are reused until they are old,
    then refreshed; a fresh login happens only when there is no usable cache.
    """
    cached = {}
    if path and os.path.exists(path):
        try:
            cached = json.load(open(path))
        except (OSError, ValueError):
            cached = {}
    fresh = None
    stamp = cached.get("at")
    if (
        stamp
        and cached.get("accessJwt")
        and now - datetime.fromisoformat(stamp) < ACCESS_TTL
    ):
        return cached["did"], cached["accessJwt"]
    if cached.get("refreshJwt"):
        try:
            fresh = xrpc(
                pds, "com.atproto.server.refreshSession", {}, cached["refreshJwt"]
            )
        except RuntimeError as exc:
            print(f"refresh failed ({exc}); logging in again")
    if not fresh:
        fresh = xrpc(
            pds,
            "com.atproto.server.createSession",
            {
                "identifier": os.environ["ATPROTO_HANDLE"],
                "password": os.environ["ATPROTO_PASSWORD"],
            },
        )
    if path:
        with open(path, "w") as f:
            json.dump(
                {
                    "did": fresh["did"],
                    "accessJwt": fresh["accessJwt"],
                    "refreshJwt": fresh["refreshJwt"],
                    "at": now.isoformat(),
                },
                f,
            )
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write("session-changed=true\n")
    return fresh["did"], fresh["accessJwt"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--session-file", help="cached session JSON, read and rewritten in place"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print records instead of writing"
    )
    args = parser.parse_args()
    now = datetime.now(UTC)
    name = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    # Events reach the PDS from the webhook receiver (scripts/floor_receiver)
    # within a second of happening; this workflow writes only the snapshot.
    snap = snapshot(now) if name in SNAPSHOT_EVENTS else None
    if not snap:
        print(f"nothing to publish for {name}")
        return 0
    if args.dry_run:
        print(json.dumps({**snap, "open": len(snap["open"])}, indent=2))
        return 0
    pds = os.environ["ATPROTO_PDS"]
    did, token = session(pds, args.session_file, now)
    wrote, previous = put_snapshot(pds, did, token, snap)
    if wrote:
        moved = flow(previous["open"], snap["open"]) if previous else None
        put_day(pds, did, token, day_record(snap, now), moved)
    print("published snapshot" if wrote else "a newer snapshot was already published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
