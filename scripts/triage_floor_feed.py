"""Publish fastmcp triage activity as atproto records for the live triage floor.

Each run turns the GitHub event that triggered it into one small public record,
and, when the event can change what is open, rewrites a snapshot of every open
issue and PR. Viewers of the floor subscribe to these records over Jetstream, so
they get each change pushed to them without calling GitHub themselves.

Only public data goes out: titles, numbers, labels, logins and timestamps that
GitHub already shows to anyone. Pushes and branch activity are not published.
"""

import argparse
import json
import os
import re
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
        elif action in {"ready_for_review", "synchronize"}:
            kind = "updated"
    elif name == "issue_comment" and action == "created":
        kind = "botcomment" if base["bot"] else "comment"
    elif name in {"pull_request_review", "pull_request_review_comment"}:
        kind = "botcomment" if base["bot"] else "review"
        base["pr"] = True
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


def gh(path: str, *, paginate: bool = False):
    command = ["gh", "api", path]
    if paginate:
        command += ["--paginate", "--slurp"]
    out = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=60
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


def put_snapshot(pds: str, did: str, token: str, snap: dict) -> bool:
    """Write the snapshot unless a run that collected later already did.

    Runs overlap, so a run that read GitHub before a close could finish after
    the run that saw it. Compare-and-swap on the record's CID, and give way to
    any snapshot collected after ours.
    """
    for _ in range(3):
        current = xrpc_get(
            pds,
            "com.atproto.repo.getRecord",
            {"repo": did, "collection": f"{NSID}.snapshot", "rkey": "self"},
        )
        if current and current["value"].get("updatedAt", "") >= snap["updatedAt"]:
            return False
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
            return True
        except RuntimeError as exc:
            if "HTTP 400" not in str(exc):  # InvalidSwap: someone wrote first
                raise
    return False


def recent_reviews(now: datetime) -> list[tuple[str, dict]]:
    """Reviews from the last two hours, read from the repo's event feed.

    Reviews on fork PRs run their workflow without secrets, so they are
    collected here by trusted runs instead of from their own trigger.
    """
    out = []
    for e in gh(f"repos/{REPO}/events?per_page=100"):
        if e.get("type") != "PullRequestReviewEvent":
            continue
        at = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00"))
        if now - at > timedelta(hours=2):
            continue
        payload = {**e.get("payload", {}), "sender": e.get("actor") or {}}
        record = to_event("pull_request_review", payload, at)
        if record:
            out.append((f"rv{e['id']}", record))
    return out


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


def rkey_for(run_id: str, attempt: str = "1") -> str:
    key = re.sub(r"[^A-Za-z0-9]", "", f"gh{run_id}x{attempt}")
    return key or "gh0"


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
    path = os.environ.get("GITHUB_EVENT_PATH")
    payload = json.load(open(path)) if path and os.path.exists(path) else {}
    event = to_event(name, payload, now)
    snap = snapshot(now) if name in SNAPSHOT_EVENTS else None
    if not event and not snap:
        print(f"nothing to publish for {name}/{payload.get('action', '')}")
        return 0
    if args.dry_run:
        print(
            json.dumps(
                {
                    "event": event,
                    "snapshot": snap and {**snap, "open": len(snap["open"])},
                },
                indent=2,
            )
        )
        return 0
    pds = os.environ["ATPROTO_PDS"]
    did, token = session(pds, args.session_file, now)
    writes = []
    if event:
        rkey = rkey_for(
            os.environ.get("GITHUB_RUN_ID", ""),
            os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        )
        writes.append(
            {
                "$type": "com.atproto.repo.applyWrites#create",
                "collection": f"{NSID}.event",
                "rkey": rkey,
                "value": event,
            }
        )
    if snap:
        put_snapshot(pds, did, token, snap)
    listed = xrpc_get(
        pds,
        "com.atproto.repo.listRecords",
        {"repo": did, "collection": f"{NSID}.event", "limit": "100"},
    )
    seen = {r["uri"].rsplit("/", 1)[-1] for r in (listed or {}).get("records", [])}
    for rkey, record in recent_reviews(now):
        if rkey not in seen:
            # putRecord, not create: two overlapping runs may both pick it up
            body = {
                "repo": did,
                "collection": f"{NSID}.event",
                "rkey": rkey,
                "record": record,
                "validate": False,
            }
            xrpc(pds, "com.atproto.repo.putRecord", body, token)
    if writes:
        xrpc(
            pds,
            "com.atproto.repo.applyWrites",
            {"repo": did, "writes": writes, "validate": False},
            token,
        )
    print(
        f"published {'event ' + event['kind'] if event else 'no event'}{' + snapshot' if snap else ''}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
