"""Public facts about the people behind an issue, for the assess role.

A maintainer glances at who reported a bug and who sent the fix: followers,
account age, history in this repository, PR activity elsewhere. These cards put
the same facts in front of the agent. A lookup that fails leaves `unavailable`
on that card instead of failing the assessment.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlencode

Read = Callable[[str], dict]
REPO = "PrefectHQ/fastmcp"
MAX_PEOPLE = 7
ELSEWHERE_DAYS = 182


def _count(read: Read, query: str, per_page: int = 1) -> dict:
    return read("search/issues?" + urlencode({"q": query, "per_page": per_page}))


def card(login: str, read: Read, now: datetime) -> dict:
    try:
        user = read(f"users/{login}")
        since = (now - timedelta(days=ELSEWHERE_DAYS)).date().isoformat()
        merged = _count(read, f"repo:{REPO} is:pr author:{login} is:merged")
        unmerged = _count(
            read, f"repo:{REPO} is:pr author:{login} is:closed is:unmerged"
        )
        elsewhere = _count(
            read, f"is:pr author:{login} -repo:{REPO} created:>={since}", 100
        )
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        return {"login": login, "unavailable": type(error).__name__}
    repos = {item["repository_url"] for item in elsewhere.get("items", [])}
    return {
        "login": login,
        "account_created": user["created_at"][:10],
        "followers": user["followers"],
        "public_repos": user["public_repos"],
        "prs_merged_here": merged["total_count"],
        "prs_closed_unmerged_here": unmerged["total_count"],
        "prs_elsewhere_last_6_months": elsewhere["total_count"],
        "repos_in_newest_100_of_those": len(repos),
    }


def _hours(start: str, end: str) -> float:
    delta = datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(
        start.replace("Z", "+00:00")
    )
    return round(delta.total_seconds() / 3600, 1)


def people(evidence: dict, read: Read, now: datetime) -> dict:
    issue = evidence["issue"]
    reporter = (issue.get("user") or {}).get("login")
    authors = [
        pr["author"].get("login")
        for pr in evidence["prs"]
        if pr["author"].get("type") != "Bot"
    ]
    logins = [login for login in dict.fromkeys([reporter, *authors]) if login]
    logins = logins[:MAX_PEOPLE]
    cards = {login: card(login, read, now) for login in logins}
    try:
        opened = read(f"repos/{REPO}/issues/{issue['number']}")["created_at"]
        timing = {
            pr["number"]: _hours(
                opened, read(f"repos/{REPO}/pulls/{pr['number']}")["created_at"]
            )
            for pr in evidence["prs"]
            if reporter and pr["author"].get("login") == reporter
        }
    except (RuntimeError, ValueError, KeyError, OSError):
        opened, timing = None, {}
    return {
        "issue_author": reporter,
        "issue_opened_at": opened,
        "reporter_prs_opened_hours_after_issue": timing,
        "cards": list(cards.values()),
    }
