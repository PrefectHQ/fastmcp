"""Weekly backlog sweep. No model runs.

`propose` lists inactive external issues and PRs in Slack and writes nothing: a
maintainer adds `sweep:close` to the ones that should go. `close` (daily) closes
items whose label has been on for 48 hours with no comment from a person since.
Triage never applies the label itself, because idleness alone can't tell an
abandoned report from a confirmed bug nobody has gotten to yet.

Thresholds live in the Prefect Variable `fastmcp_triage_config` under `sweep`, and
`github_writes` ("off", "dry_run", "on"; default "dry_run") gates every write, so
neither needs a deploy to change. Writes go out as the triage GitHub App.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from prefect import flow, get_run_logger
from prefect.context import FlowRunContext
from prefect.events import emit_event
from prefect.variables import Variable
from pydantic import BaseModel, ConfigDict, Field

from flows import agentic_telemetry  # noqa: F401 - sends spans to Logfire
from flows.agentic_github import GitHub, can_write
from flows.agentic_labels import CONFIG_VARIABLE, mode
from flows.agentic_review_app import ReviewApp, review_client
from flows.agentic_stack_runtime import secret_sync

REPOSITORY = "PrefectHQ/fastmcp"
SWEEP_LABEL = "sweep:close"
MAINTAINERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
SWEEP_PERMISSIONS = {"issues": "write", "pull_requests": "write"}
ISSUE_URL = f"https://github.com/{REPOSITORY}/issues/"

CLOSE_COMMENT = (
    "Closing this since it has been inactive for a while. If it still matters, "
    "comment here with what's changed and a maintainer can reopen it."
)


class SweepConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    issue_idle_days: int = Field(default=30, ge=7)
    pr_idle_days: int = Field(default=21, ge=7)
    max_items: int = Field(default=25, ge=1, le=100)
    veto_hours: int = Field(default=48, ge=24)
    protected_labels: list[str] = Field(
        default_factory=lambda: [
            "security",
            "high-priority",
            "proposal",
            "breaking change",
            "DON'T MERGE",
            "bypass-issue-check",
            "prs welcome",
        ]
    )


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def candidates(
    items: list[dict],
    config: SweepConfig,
    now: datetime,
    maintainer: Callable[[str], bool] = lambda login: False,
) -> list[dict]:
    """Open items from outside contributors that nobody has touched in a while.

    `author_association` misses maintainers whose org membership is private, so
    `maintainer` checks effective repository permission for the survivors.
    """
    protected = set(config.protected_labels) | {SWEEP_LABEL}
    picked = []
    for item in items:
        labels = {label.get("name") for label in item.get("labels") or []}
        is_pr = "pull_request" in item
        idle = config.pr_idle_days if is_pr else config.issue_idle_days
        if (
            item.get("state") != "open"
            or (item.get("user") or {}).get("type") != "User"
            or item.get("author_association") in MAINTAINERS
            or item.get("assignees")
            or labels & protected
            or parse_time(item["updated_at"]) > now - timedelta(days=idle)
        ):
            continue
        picked.append(item)
    picked.sort(key=lambda item: item["updated_at"])
    kept = []
    for item in picked:
        if len(kept) == config.max_items:
            break
        if not maintainer((item.get("user") or {}).get("login") or ""):
            kept.append(item)
    return kept


def labeled_at(timeline: list[dict]) -> datetime | None:
    times = [
        parse_time(event["created_at"])
        for event in timeline
        if event.get("event") == "labeled"
        and (event.get("label") or {}).get("name") == SWEEP_LABEL
    ]
    return max(times) if times else None


def vetoed_by_activity(timeline: list[dict], since: datetime) -> bool:
    """A comment from a person after labeling keeps the item open."""
    return any(
        event.get("event") == "commented"
        and (event.get("user") or {}).get("type") == "User"
        and parse_time(event["created_at"]) > since
        for event in timeline
    )


def due(item: dict, timeline: list[dict], config: SweepConfig, now: datetime) -> bool:
    since = labeled_at(timeline)
    return (
        since is not None
        and since <= now - timedelta(hours=config.veto_hours)
        and not vetoed_by_activity(timeline, since)
    )


def line(item: dict) -> str:
    kind = "PR" if "pull_request" in item else "issue"
    title = " ".join(str(item.get("title") or "").split())[:60]
    title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    who = (item.get("user") or {}).get("login") or "?"
    idle = item["updated_at"][:10]
    return f"<{ISSUE_URL}{item['number']}|#{item['number']}> {kind} · {title} · @{who} · idle since {idle}"


def slack(text: str) -> None:
    context = FlowRunContext.get()
    owner = context.flow_run.id if context and context.flow_run else "local"
    emit_event(
        event="fastmcp.triage.outcome",
        # The Slack automation (fastmcp-triage-outcome) matches fastmcp.triage.intake.*
        resource={"prefect.resource.id": f"fastmcp.triage.intake.sweep-{owner}"},
        payload={"text": text},
    )


def is_maintainer(read: GitHub, login: str) -> bool:
    """Unknown or unreadable permission counts as a maintainer, so it is never swept."""
    try:
        return can_write(read.request, login)
    except (ValueError, OSError):
        return True


def propose(read: GitHub, config: SweepConfig, now: datetime) -> list[dict]:
    """Candidates for a maintainer to label; nothing is written."""
    items = read.pages(
        f"repos/{REPOSITORY}/issues?state=open&sort=updated&direction=asc"
    )
    return candidates(items, config, now, lambda login: is_maintainer(read, login))


def close(
    read: GitHub,
    write: Callable[..., object] | None,
    config: SweepConfig,
    now: datetime,
) -> list[dict]:
    items = read.pages(
        f"repos/{REPOSITORY}/issues?state=open&labels={SWEEP_LABEL.replace(':', '%3A')}"
    )
    closed = []
    for item in items:
        timeline = read.pages(f"repos/{REPOSITORY}/issues/{item['number']}/timeline")
        if not due(item, timeline, config, now):
            continue
        if write is not None:
            write(
                f"repos/{REPOSITORY}/issues/{item['number']}/comments",
                {"body": CLOSE_COMMENT},
            )
            write(
                f"repos/{REPOSITORY}/issues/{item['number']}",
                {"state": "closed", "state_reason": "not_planned"}
                if "pull_request" not in item
                else {"state": "closed"},
                method="PATCH",
            )
        closed.append(item)
    return closed


@flow(name="fastmcp-triage-sweep", timeout_seconds=1200)
def sweep(phase: Literal["propose", "close"], reviewer_app: ReviewApp) -> dict:
    logger = get_run_logger()
    raw = Variable.get(CONFIG_VARIABLE, default={})
    config = (
        SweepConfig.model_validate((raw or {}).get("sweep") or {})
        if isinstance(raw, dict)
        else SweepConfig()
    )
    current_mode = mode(raw, "sweep")
    if current_mode == "off":
        return {"phase": phase, "mode": current_mode, "items": []}
    now = datetime.now(UTC)
    read = GitHub(secret_sync("fastmcp-triage-gh-token"))
    prefix = "" if current_mode == "on" else "(dry run) "

    def run(write: Callable[..., object] | None) -> list[dict]:
        if phase == "propose":
            return propose(read, config, now)
        return close(read, write, config, now)

    if current_mode == "on":
        with review_client(
            reviewer_app,
            secret_sync(reviewer_app.private_key_block),
            permissions=SWEEP_PERMISSIONS,
        ) as client:
            items = run(client.request)
    else:
        items = run(None)
    numbers = [item["number"] for item in items]
    logger.info("%s%s: %s", prefix, phase, numbers)
    if phase == "propose" and items:
        header = (
            f"*Weekly sweep:* {len(items)} inactive items. Nothing is labeled: add "
            f"`{SWEEP_LABEL}` to any that should go, and they close {config.veto_hours}h later."
        )
        slack("\n".join([header, *map(line, items)]))
    if phase == "close" and items:
        slack(
            f"{prefix}*Sweep closed* "
            + ", ".join(f"<{ISSUE_URL}{n}|#{n}>" for n in numbers)
        )
    return {"phase": phase, "mode": current_mode, "items": numbers}
