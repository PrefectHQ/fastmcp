from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

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

    def scoped(self) -> bool:
        return bool(self.issues or self.pull_requests)

    def scope(self) -> str:
        if self.queue:
            return "fastmcp-intake-" + self.queue
        return "fastmcp-intake-" + digest([self.branch, sorted(set(self.issues))])[:24]


REFERENCE = re.compile(
    r"(?:(?<![\w/])#|github\.com/PrefectHQ/fastmcp/issues/)([1-9][0-9]*)\b"
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
