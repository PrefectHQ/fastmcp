"""Public review leads from GitHub inventory and fully paginated discussion.

No private judgments, model calls, or write credentials. A lead is not a verdict.
"""

import argparse
import hashlib
import json
import re
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

REPO = "PrefectHQ/fastmcp"
MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}
HOLD = re.compile(r"\b(?:dnm|draft|do[ -]?not[ -]?merge|don['’]?t[ -]?merge)\b", re.I)


class GitHubReadError(RuntimeError):
    """Only allowlisted diagnostics may reach public output, never raw stderr."""

    def __init__(self, kind: str, attempts: int, status: int | None = None):
        super().__init__(kind)
        self.diagnostic = {"kind": kind, "attempts": attempts, "http_status": status}


def github(path: str, *, paginate: bool = False) -> dict | list:
    command = ["gh", "api", f"repos/{REPO}/{path}"]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    for attempt in (1, 2):
        status = None
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=True, timeout=15
            )
            value = json.loads(result.stdout)
            if paginate:
                if not isinstance(value, list) or any(
                    not isinstance(p, list) for p in value
                ):
                    raise GitHubReadError("invalid_response", attempt)
                return [item for page in value for item in page]
            if not isinstance(value, dict):
                raise GitHubReadError("invalid_response", attempt)
            return value
        except subprocess.TimeoutExpired:
            kind = "timeout"
        except json.JSONDecodeError:
            kind = "invalid_json"
        except subprocess.CalledProcessError as exc:
            # Extract only a numeric status and fixed categories. API messages can
            # contain arbitrary user content or credentials; do not retain them.
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            match = re.search(r"\(HTTP ([1-5][0-9]{2})\)", stderr)
            status = int(match[1]) if match else None
            kind = "http_error" if status else "command_failed"
            if not status and "looking for beginning of value" in stderr:
                kind = "invalid_json"
        except OSError:
            raise GitHubReadError("client_unavailable", attempt) from None
        if attempt == 2 or not (
            kind in {"timeout", "invalid_json"}
            or status == 429
            or (status and status >= 500)
        ):
            raise GitHubReadError(kind, attempt, status) from None
        time.sleep(1)
    raise AssertionError("Unreachable retry loop")


def diagnostic(error: Exception, stage: str) -> dict:
    value = (
        error.diagnostic
        if isinstance(error, GitHubReadError)
        else {"kind": "invalid_response", "attempts": 1, "http_status": None}
    )
    return {"stage": stage, **value}


def review_binding(review: dict, number: int) -> dict | None:
    marker = re.match(
        r"^<!-- fastmcp-triage-review:v1 pr:([0-9]+) head:([a-f0-9]{40}) base:([a-f0-9]{40}) -->",
        review.get("body") or "",
    )
    if (
        not marker
        or int(marker[1]) != number
        or review.get("state") != "COMMENTED"
        or review.get("commit_id") != marker[2]
    ):
        return None
    return {
        "review_id": review.get("id"),
        "head_sha": marker[2],
        "baseline_sha": marker[3],
        "body_sha256": hashlib.sha256(review["body"].encode()).hexdigest(),
    }


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
        "pr_collected_at": datetime.now(UTC).isoformat(),
        "eligible_at_read": pr["state"] == "open" and not pr["draft"] and not held(pr),
        "activity": {"status": "unavailable"},
        "collection_diagnostics": [],
    }
    # Do not nominate work that was closed, drafted or held after inventory read.
    if not result["eligible_at_read"]:
        return result
    result["baseline_sha"] = None
    try:
        branch = github(f"branches/{quote(pr['base']['ref'], safe='')}")
        assert isinstance(branch, dict)
        baseline = branch["commit"]["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", baseline):
            raise ValueError("Invalid target SHA")
        result["baseline_sha"] = baseline
        result["baseline_collected_at"] = datetime.now(UTC).isoformat()
    except (GitHubReadError, ValueError, KeyError, AssertionError) as exc:
        result["collection_diagnostics"].append(diagnostic(exc, "target_baseline"))
    try:
        result["activity"] = read_activity(number, pr, result["baseline_sha"])
    except (
        GitHubReadError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        result["collection_diagnostics"].append(diagnostic(exc, "discussion"))
        result["collection_gap"] = "Discussion collection failed; inspect GitHub"
    return result


def read_activity(number: int, pr: dict, baseline: str | None) -> dict:
    comments = github(f"issues/{number}/comments?per_page=100", paginate=True)
    reviews = github(f"pulls/{number}/reviews?per_page=100", paginate=True)
    if (
        not isinstance(comments, list)
        or not isinstance(reviews, list)
        or any(not isinstance(value, dict) for value in [*comments, *reviews])
    ):
        raise ValueError("Invalid discussion shape")
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
        and a["user"].get("type") == "Bot"
    ]
    bindings = [
        binding for review in bot_reviews if (binding := review_binding(review, number))
    ]
    return {
        "status": "collected",
        "pagination": "all_returned_pages",
        "comments": len(comments),
        "reviews": len(reviews),
        "first_maintainer_account_response_at": min(human, default=None),
        "last_maintainer_account_activity_at": max(human, default=None),
        "triage_app_review_seen": bool(bot_reviews),
        "triage_app_review_on_snapshot_revision": (
            any(
                b["head_sha"] == pr["head"]["sha"] and b["baseline_sha"] == baseline
                for b in bindings
            )
            if baseline is not None
            else None
        ),
        "triage_app_review_bindings": bindings,
        "collected_at": datetime.now(UTC).isoformat(),
    }


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
    leads: list[dict] = []
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
        except (
            GitHubReadError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            AssertionError,
        ) as exc:
            lead["collection_diagnostics"] = [diagnostic(exc, "pr_metadata")]
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
        "collection_finished_at": datetime.now(UTC).isoformat(),
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
        "lead_collection_status": "partial"
        if any(
            lead["activity"]["status"] != "collected"
            or lead.get("collection_diagnostics")
            for lead in leads
        )
        else "collected",
        "review_leads": leads,
        "limitations": [
            "GitHub reads are sequential, not an atomic snapshot. Recheck current head, holds and discussion before acting.",
            "Discussion includes issue comments and submitted reviews, not inline review threads or all timeline activity.",
            "Maintainer account activity can be agent-assisted; it is not acknowledgement, approval or disposition.",
            "A triage-app review, including on the snapshot head and target baseline, does not establish executable or independent verification.",
            "Missing activity is unknown. Absence from this bounded queue does not mean handled. No closure or merge is authorized.",
        ],
    }


def main() -> None:
    from backlog_status import fetch_items, newest_number, require_complete

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = datetime.now(UTC).replace(microsecond=0)
    newest = newest_number()
    items = fetch_items()
    require_complete(items, newest)
    report = build(items, started)
    report["source_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    args.output.write_text(json.dumps(report, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
