"""Public review leads from GitHub inventory and fully paginated discussion.

No private judgments, model calls, or write credentials. A lead is not a verdict.
"""

import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone

REPO = "PrefectHQ/fastmcp"
MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}
HOLD = re.compile(r"\b(?:dnm|draft|do[ -]?not[ -]?merge|don['’]?t[ -]?merge)\b", re.I)


def github(path: str, *, paginate: bool = False) -> dict | list:
    command = ["gh", "api", f"repos/{REPO}/{path}"]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    result = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=20
    )
    value = json.loads(result.stdout)
    return [item for page in value for item in page] if paginate else value


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def held(item: dict) -> bool:
    labels = [label["name"] for label in item.get("labels", [])]
    return bool(HOLD.search(" ".join([item["title"], item.get("body") or "", *labels])))


def inspect_pr(number: int, now: datetime) -> dict:
    pr = github(f"pulls/{number}")
    assert isinstance(pr, dict)
    result = {
        "head_sha": pr["head"]["sha"],
        "base_sha": pr["base"]["sha"],
        "target": pr["base"]["ref"],
        "github_updated_at": pr["updated_at"],
        "pr_collected_at": datetime.now(timezone.utc).isoformat(),
        "eligible_at_read": pr["state"] == "open" and not pr["draft"] and not held(pr),
        "activity": {"status": "unavailable"},
    }
    # Do not nominate work that was closed, drafted or held after inventory read.
    if not result["eligible_at_read"]:
        return result
    try:
        comments = github(f"issues/{number}/comments?per_page=100", paginate=True)
        reviews = github(f"pulls/{number}/reviews?per_page=100", paginate=True)
        assert isinstance(comments, list) and isinstance(reviews, list)
    except (subprocess.SubprocessError, ValueError, AssertionError):
        result["collection_gap"] = "Discussion collection failed; inspect GitHub"
        return result
    author = pr["user"]["login"]
    human = [
        a["created_at"] if "created_at" in a else a["submitted_at"]
        for a in [*comments, *reviews]
        if a.get("author_association") in MAINTAINERS
        and (a.get("user") or {}).get("type") == "User"
        and a["user"]["login"] != author
        and (a.get("created_at") or a.get("submitted_at"))
    ]
    bot_reviews = [
        a
        for a in reviews
        if (a.get("user") or {}).get("login") == "fastmcp-triage-reviewer[bot]"
    ]
    result["activity"] = {
        "status": "collected",
        "pagination": "all_returned_pages",
        "comments": len(comments),
        "reviews": len(reviews),
        "first_maintainer_account_response_at": min(human, default=None),
        "last_maintainer_account_activity_at": max(human, default=None),
        "triage_app_review_seen": bool(bot_reviews),
        "triage_app_review_on_snapshot_head": any(
            a.get("commit_id") == pr["head"]["sha"] for a in bot_reviews
        ),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    return result


def build(items: list[dict], now: datetime, limit: int = 5) -> dict:
    age_counts = {kind: Counter() for kind in ("issues", "prs")}
    candidates = []
    for item in items:
        created = parse_time(item["created_at"])
        closed = item.get("closed_at")
        if created > now or (closed and parse_time(closed) <= now):
            continue
        kind = "prs" if item.get("pull_request") is not None else "issues"
        age = max(0, (now - parse_time(item["created_at"])).days)
        bucket = (
            "lt_7d"
            if age < 7
            else "7_30d"
            if age < 30
            else "30_90d"
            if age < 90
            else "gte_90d"
        )
        age_counts[kind][bucket] += 1
        if (
            kind == "prs"
            and item["state"] == "open"
            and not item.get("draft")
            and not held(item)
            and item.get("author_association") not in MAINTAINERS
            and (item.get("user") or {}).get("type") == "User"
        ):
            candidates.append(item)
    candidates.sort(key=lambda item: (item["created_at"], item["number"]))
    leads = []
    for item in candidates[:limit]:
        lead = {
            "number": item["number"],
            "title": item["title"],
            "url": f"https://github.com/{REPO}/pull/{item['number']}",
            "author": item["user"]["login"],
            "age_days": max(0, (now - parse_time(item["created_at"])).days),
            "activity": {"status": "unavailable"},
        }
        try:
            lead.update(inspect_pr(item["number"], now))
        except (subprocess.SubprocessError, ValueError, KeyError, AssertionError):
            # Unknown stays unknown; never turn failed collection into no response.
            lead["collection_gap"] = (
                "PR or discussion collection failed; inspect GitHub"
            )
        if lead.get("eligible_at_read") is False:
            continue
        leads.append(lead)
    return {
        "schema": "fastmcp-public-triage/1",
        "repo": REPO,
        "collection_started_at": now.isoformat(),
        "collection_finished_at": datetime.now(timezone.utc).isoformat(),
        "inventory_items": len(items),
        "open_age_buckets": {
            kind: {key: counts[key] for key in ("lt_7d", "7_30d", "30_90d", "gte_90d")}
            for kind, counts in age_counts.items()
        },
        "selection": {
            "method": "oldest open, non-draft, non-held PRs by non-maintainer user accounts",
            "eligible_in_inventory": len(candidates),
            "inspection_limit": limit,
            "returned": len(leads),
        },
        "review_leads": leads,
        "limitations": [
            "GitHub reads are sequential, not an atomic snapshot. Recheck current head, holds and discussion before acting.",
            "Discussion includes issue comments and submitted reviews, not inline review threads or all timeline activity.",
            "Maintainer account activity can be agent-assisted; it is not acknowledgement, approval or disposition.",
            "A triage-app review, including on the snapshot head, does not establish executable or independent verification.",
            "Missing activity is unknown. Absence from this bounded queue does not mean handled. No closure or merge is authorized.",
        ],
    }
