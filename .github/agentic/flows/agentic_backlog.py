from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from flows.agentic_intake import directly_addresses
from flows.agentic_labels import AREA_LABELS as TRIAGE_AREAS
from flows.agentic_labels import TRIAGE_LABEL
from flows.agentic_stack import digest


class Intake(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    branch: str = Field(default="main", pattern=r"^(main|release/[23]\.x)$")
    max_items: int = Field(default=3, ge=1, le=3)
    refresh_minutes: int = Field(default=60, ge=1, le=1440)
    issues: list[PositiveInt] = Field(default_factory=list, max_length=30)
    pull_requests: list[PositiveInt] = Field(default_factory=list, max_length=10)
    queue: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,32}$")
    retry_failed: bool = False
    # GitHub login whose event started this intake; maintainers' own PR events are skipped.
    sender: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")

    def scoped(self) -> bool:
        return bool(self.issues or self.pull_requests)

    def scope(self) -> str:
        identity = [
            self.branch,
            sorted(set(self.issues)),
            sorted(set(self.pull_requests)),
        ]
        if self.queue:
            if self.scoped():
                return f"fastmcp-intake-{self.queue}-{digest(identity)[:24]}"
            return f"fastmcp-intake-{self.queue}-{digest([self.branch])[:12]}"
        return "fastmcp-intake-" + digest(identity)[:24]


# `&` excludes HTML character references such as Renovate's `&#8203;`.
REFERENCE = re.compile(
    r"(?:(?<![\w/&])#|github\.com/PrefectHQ/fastmcp/issues/)([1-9][0-9]*)\b"
)


def referenced_issues(body: str) -> list[int]:
    return sorted({int(number) for number in REFERENCE.findall(body)})[:5]


def inventory(read: Callable[[str], object], config: Intake, page: int) -> dict:
    root = "repos/PrefectHQ/fastmcp"
    if config.scoped():
        numbers = set(config.issues)
        for number in sorted(set(config.pull_requests)):
            pull = read(f"{root}/pulls/{number}")
            if not isinstance(pull, dict):
                raise ValueError("Invalid pull request metadata")
            # The issue-link gate closes most outside PRs seconds after they open;
            # by the time intake runs there's no contribution left to verify.
            if pull.get("state") == "closed":
                continue
            numbers.update(referenced_issues(pull.get("body") or ""))
        rows = [read(f"{root}/issues/{number}") for number in sorted(numbers)]
        pages = {"scoped": len(rows), "pull_requests": len(set(config.pull_requests))}
        next_page = 1
    else:
        backlog = read(
            f"{root}/issues?state=open&sort=created&direction=asc&per_page=30&page={page}"
        )
        recent = read(
            f"{root}/issues?state=open&sort=updated&direction=desc&per_page=30&page=1"
        )
        if not isinstance(backlog, list) or not isinstance(recent, list):
            raise ValueError("Expected bounded GitHub issue pages")
        if len(backlog) > 30 or len(recent) > 30:
            raise ValueError("GitHub page exceeded the inventory bound")
        rows = [*backlog, *recent]
        pages = {
            "backlog_page": page,
            "backlog_rows": len(backlog),
            "recent_rows": len(recent),
        }
        next_page = page + 1 if len(backlog) == 30 else 1
    issues = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("number"), int):
            raise ValueError("Invalid issue metadata")
        if row.get("pull_request") or row["state"] != "open":
            continue
        metadata = {
            name: row[name] for name in ("number", "title", "updated_at", "state")
        }
        metadata["labels"] = sorted(label["name"] for label in row["labels"])
        metadata["assignees"] = sorted(actor["login"] for actor in row["assignees"])
        issues[row["number"]] = {
            "issue_number": row["number"],
            "title": row["title"],
            "metadata_revision": digest(metadata),
            "attempts": 0,
        }
    return {
        "pending": list(issues.values()),
        "next_page": next_page,
        "coverage": {
            **pages,
            "metadata_rows": len(rows),
            "unique_open_issues": len(issues),
            "scope": "explicit issues"
            if config.scoped()
            else "rolling open-issue pages",
            "limitation": "Rolling pages are not a snapshot census. Concurrent changes can shift pages; later sweeps revisit them. PR activity is checked during full intake, not inferred from issue timestamps.",
        },
    }


HANDLED_KEY = "fastmcp-triage-handled-{number}"


def handled_key(number: int) -> str:
    return HANDLED_KEY.format(number=number)


MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def contributions(pulls: list[dict]) -> list[dict]:
    """Open, ready pull requests from people outside the maintainers.

    Maintainers' own PRs and bot PRs are not triage's to review.
    """
    return [
        pull
        for pull in pulls
        if pull.get("state") == "open"
        and not pull.get("draft")
        and (pull.get("user") or {}).get("type") == "User"
        and pull.get("author_association") not in MAINTAINER_ASSOCIATIONS
    ]


def addressed(pulls: list[dict]) -> dict[int, list[dict]]:
    """Issue number -> contributions that say they fix it."""
    found: dict[int, list[dict]] = {}
    for pull in contributions(pulls):
        body = pull.get("body") or ""
        for number in referenced_issues(body):
            if directly_addresses(body, number):
                found.setdefault(number, []).append(pull)
    return found


def handoff_revision(issue: dict, pulls: list[dict]) -> str:
    """What a handed-off issue looks like to the person who has to act on it.

    It changes when anyone, person or bot, edits, comments on, labels, assigns,
    closes or reopens the issue, or opens, pushes to, reopens, drafts or closes a
    contribution that fixes it. Triage's own labels are left out, so a run never
    makes its own issue look changed.
    """
    number = issue["number"]
    own = {TRIAGE_LABEL, *TRIAGE_AREAS}
    return digest(
        {
            "number": number,
            "state": issue.get("state"),
            "title": issue.get("title"),
            "body": digest(issue.get("body") or ""),
            "comments": issue.get("comments", 0),
            "labels": sorted(
                label["name"]
                for label in issue.get("labels") or []
                if label["name"] not in own
            ),
            "assignees": sorted(
                actor["login"] for actor in issue.get("assignees") or []
            ),
            "contributions": sorted(
                [
                    pull["number"],
                    (pull.get("head") or {}).get("sha"),
                    pull.get("comments"),
                ]
                for pull in addressed(pulls).get(number, [])
            ),
        }
    )


def open_pulls(read: Callable[[str], object], pages: int = 3) -> list[dict]:
    pulls: list[dict] = []
    for page in range(1, pages + 1):
        rows = read(
            f"repos/PrefectHQ/fastmcp/pulls?state=open&per_page=100&page={page}"
        )
        if not isinstance(rows, list):
            raise ValueError("Expected a page of pull requests")
        pulls.extend(row for row in rows if isinstance(row, dict))
        if len(rows) < 100:
            return with_discussion(read, pulls)
    raise ValueError("More open pull requests than the follow-through bound")


def with_discussion(read: Callable[[str], object], pulls: list[dict]) -> list[dict]:
    """Add each PR's conversation comment count, which the pulls listing leaves out.

    A contributor answering on their PR is a change to the hand-off. Triage's own
    output on a PR is a review, which this count doesn't include.
    """
    counts: dict[int, int] = {}
    for page in range(1, 11):
        rows = read(
            f"repos/PrefectHQ/fastmcp/issues?state=open&per_page=100&page={page}"
        )
        if not isinstance(rows, list):
            raise ValueError("Expected a page of issues")
        for row in rows:
            if isinstance(row, dict) and "pull_request" in row:
                counts[row["number"]] = row.get("comments", 0)
        if len(rows) < 100:
            return [{**pull, "comments": counts.get(pull["number"])} for pull in pulls]
    raise ValueError("More open issues than the follow-through bound")


def handoffs(read: Callable[[str], object], numbers: list[int]) -> dict[str, str]:
    """The hand-off revision of each issue as it is now, keyed by issue number.

    Taken when intake admits the issues, so anything that changes while triage
    works on them shows up as a change on the next follow-through pass.
    """
    if not numbers:
        return {}
    pulls = open_pulls(read)
    found = {}
    for number in numbers:
        issue = read(f"repos/PrefectHQ/fastmcp/issues/{number}")
        if isinstance(issue, dict) and isinstance(issue.get("number"), int):
            found[str(number)] = handoff_revision(issue, pulls)
    return found


def due(
    receipt: dict | None, item: dict, config: Intake, now: datetime, source: str
) -> bool:
    if receipt is None or receipt["metadata_revision"] != item["metadata_revision"]:
        return True
    if receipt["status"] == "operational":
        return config.retry_failed or receipt["source_sha"] != source
    return now >= datetime.fromisoformat(receipt["processed_at"]) + timedelta(
        minutes=config.refresh_minutes
    )


def settle(
    pending: list[dict], selected: list[int], result: dict
) -> tuple[list[dict], list[int]]:
    errors = result["bins"]["operational"]
    moving = {
        item.get("issue_number")
        for item in errors
        if item.get("status") == "baseline_changed"
    }
    failed = {item.get("issue_number") for item in errors}
    if any(number is None for number in failed):
        failed.update(selected)
    seen = set()
    for name, items in result["bins"].items():
        if name == "operational":
            continue
        for item in items:
            number = item.get("issue_number")
            if item.get("status") in {"stale", "baseline_changed"} or any(
                check["status"] == "environment_failure"
                for check in item.get("verification", [])
            ):
                failed.add(number)
                if item.get("status") == "baseline_changed":
                    moving.add(number)
            else:
                seen.add(number)
    completed = [n for n in selected if n in seen and n not in failed]
    remaining = []
    for item in pending:
        if item["issue_number"] in completed:
            continue
        if item["issue_number"] in selected and item["issue_number"] not in moving:
            item = {**item, "attempts": item["attempts"] + 1}
        remaining.append(item)
    remaining.sort(key=lambda item: item["issue_number"] in moving)
    return remaining, completed
