"""Triage's Slack voice: an agent reads the day's state and decides what to say.

Intake no longer posts a line per issue. When it leaves an issue for a maintainer it
labels it `needs:triage` and records the next step under `fastmcp-triage-next-<n>`.
This flow gathers that queue, the last day of intake runs and what was already
posted, and hands them to the `digest` agent role. The agent decides whether anything
is worth a message and writes it; the flow only posts what the agent returns.

It also gathers the contributions the issue-link gate closed. The gate closes an
outside PR unless its author is assigned the issue it fixes, and reopens it once they
are, so each one waits on a maintainer to assign the contributor or decline, and
nothing else would ever ask him (#6820, #6821).

`morning` runs once a day for the digest. `check` runs every few hours and posts only
when the agent finds something that shouldn't wait for the morning.

The agent plan comes from `fastmcp_triage_config.digest` ({"flow_id", "version_id"});
without it the flow does nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from prefect import flow, get_run_logger
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.client.schemas.filters import (
    ArtifactFilter,
    DeploymentFilter,
    FlowRunFilter,
)
from prefect.client.schemas.sorting import ArtifactSort
from prefect.context import FlowRunContext
from prefect.events import emit_event
from prefect.variables import Variable

from flows.agentic_backlog import MAINTAINER_ASSOCIATIONS, referenced_issues
from flows.agentic_github import GitHub
from flows.agentic_intake import directly_addresses
from flows.agentic_labels import CONFIG_VARIABLE, TRIAGE_LABEL
from flows.agentic_stack_runtime import AgentPlan, judge, secret_sync
from flows.agentic_vouch import load_standing

REPOSITORY = "PrefectHQ/fastmcp"
QUEUE_URL = f"https://github.com/{REPOSITORY}/issues?q=" + quote(
    f'is:open label:"{TRIAGE_LABEL}"'
)
NEXT_KEY = "fastmcp-triage-next-{number}"
POSTED_KEY = "fastmcp-triage-digest-posted"
GATE_LABEL = "missing-issue-link"
# How far back closed contributions still count as waiting, and how much to show.
GATE_WINDOW = timedelta(days=30)
GATE_PAGES = 5
GATE_SENDERS = 10
GATE_PER_SENDER = 5
STANDING_ORDER = {"vouched": 0, "denounced": 2}


def next_key(number: int) -> str:
    return NEXT_KEY.format(number=number)


def record_next_step(owner: UUID, number: int, line: str) -> None:
    """Keep the latest next step for an issue so the digest can say it."""
    with get_client(sync_client=True) as client:
        client.create_artifact(
            ArtifactCreate(
                key=next_key(number),
                flow_run_id=owner,
                type="fastmcp-triage-next",
                data={"line": line, "at": datetime.now(UTC).isoformat()},
            )
        )


def artifacts(key: str, limit: int) -> list[dict]:
    with get_client(sync_client=True) as client:
        records = client.read_artifacts(
            artifact_filter=ArtifactFilter(key={"any_": [key]}),
            sort=ArtifactSort.CREATED_DESC,
            limit=limit,
        )
    return [record.data for record in records if isinstance(record.data, dict)]


def waiting(pages: Callable[[str], list[dict]]) -> list[dict]:
    """Every open issue labeled `needs:triage`, oldest first."""
    found = pages(
        f"repos/{REPOSITORY}/issues?state=open&labels={quote(TRIAGE_LABEL)}"
        "&sort=created&direction=asc"
    )
    return [item for item in found if "pull_request" not in item]


def queue_entry(item: dict, now: datetime) -> dict:
    number = item["number"]
    created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
    steps = artifacts(next_key(number), 1)
    return {
        "number": number,
        "title": item.get("title"),
        "author": (item.get("user") or {}).get("login"),
        "labels": sorted(label.get("name") for label in item.get("labels") or []),
        "age_days": (now - created).days,
        "comments": item.get("comments", 0),
        "next_step": steps[0].get("line") if steps else None,
    }


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def gate_closed_pulls(read: Callable[[str], object], since: datetime) -> list[dict]:
    """Outside PRs the issue-link gate closed since `since`, newest first."""
    found = []
    for page in range(1, GATE_PAGES + 1):
        batch = read(
            f"repos/{REPOSITORY}/pulls?state=closed&sort=updated&direction=desc"
            f"&per_page=100&page={page}"
        )
        if not isinstance(batch, list):
            break
        for pull in batch:
            labels = {label.get("name") for label in pull.get("labels") or []}
            if (
                pull.get("merged_at") is None
                and GATE_LABEL in labels
                and (pull.get("user") or {}).get("type") == "User"
                and pull.get("author_association") not in MAINTAINER_ASSOCIATIONS
                and pull.get("closed_at")
                and _when(pull["closed_at"]) >= since
            ):
                found.append(pull)
        if len(batch) < 100 or _when(batch[-1]["updated_at"]) < since:
            break
    return found


def maintainer_answered(read: Callable[[str], object], pull: dict) -> bool:
    """Whether a maintainer commented on the PR after the gate closed it.

    That is a decision (usually a decline), so the PR stops waiting on one.
    """
    comments = read(f"repos/{REPOSITORY}/issues/{pull['number']}/comments?per_page=100")
    closed = _when(pull["closed_at"])
    return isinstance(comments, list) and any(
        comment.get("author_association") in MAINTAINER_ASSOCIATIONS
        and (comment.get("user") or {}).get("type") == "User"
        and _when(comment["created_at"]) > closed
        for comment in comments
    )


def gate_closed(
    read: Callable[[str], object],
    standing: Callable[[str], dict] | None,
    now: datetime,
) -> list[dict]:
    """Closed contributions waiting on a maintainer to assign their author or decline.

    A PR counts while the issue it says it fixes is open and assigned to nobody, and
    no maintainer has answered on the PR since it was closed. Assigning the author
    reopens the PR and assigning anyone else decides it, so either drops it from
    here. Grouped by contributor: reproduced issues first, then contributors the
    contributor graph vouches for.
    """
    groups: dict[str, list[dict]] = {}
    issues: dict[int, dict | None] = {}
    for pull in gate_closed_pulls(read, now - GATE_WINDOW):
        body = pull.get("body") or ""
        numbers = [n for n in referenced_issues(body) if directly_addresses(body, n)]
        waiting_on = []
        for number in numbers:
            if number not in issues:
                item = read(f"repos/{REPOSITORY}/issues/{number}")
                issues[number] = item if isinstance(item, dict) else None
            issue = issues[number]
            if (
                issue is not None
                and "pull_request" not in issue
                and issue.get("state") == "open"
                and not issue.get("assignees")
            ):
                waiting_on.append(issue)
        if not waiting_on or maintainer_answered(read, pull):
            continue
        sender = pull["user"]["login"]
        for issue in waiting_on:
            steps = artifacts(next_key(issue["number"]), 1)
            step = steps[0].get("line") if steps else None
            groups.setdefault(sender, []).append(
                {
                    "pr": pull["number"],
                    "title": pull.get("title"),
                    "closed_at": pull["closed_at"],
                    "issue": issue["number"],
                    "issue_title": issue.get("title"),
                    "issue_author": (issue.get("user") or {}).get("login"),
                    "next_step": step,
                    "reproduced": bool(step and step.lower().startswith("reproduced")),
                }
            )
    entries: list[dict] = []
    for sender, prs in groups.items():
        # Reproduced first, then the most recent: a contributor who just opened a
        # PR is still around to take an assignment.
        prs.sort(key=lambda pr: pr["closed_at"], reverse=True)
        prs.sort(key=lambda pr: not pr["reproduced"])
        known = standing(sender)["standing"] if standing else "unknown"
        entries.append(
            {
                "sender": sender,
                "standing": known,
                "count": len(prs),
                "prs": prs[:GATE_PER_SENDER],
            }
        )
    entries.sort(
        key=lambda entry: max(pr["closed_at"] for pr in entry["prs"]), reverse=True
    )
    entries.sort(
        key=lambda entry: (
            not any(pr["reproduced"] for pr in entry["prs"]),
            STANDING_ORDER.get(entry["standing"], 1),
        )
    )
    return entries[:GATE_SENDERS]


def intake_runs(since: datetime) -> dict:
    with get_client(sync_client=True) as client:
        runs = client.read_flow_runs(
            deployment_filter=DeploymentFilter(name={"any_": ["cloud-triage-intake"]}),
            flow_run_filter=FlowRunFilter(start_time={"after_": since}),
            limit=200,
        )
    states: dict[str, int] = {}
    problems = []
    for run in runs:
        if run.state is None:
            continue
        name = run.state.name or "Unknown"
        states[name] = states.get(name, 0) + 1
        if name != "Completed" and run.state.message:
            problems.append(
                {
                    "state": name,
                    "at": str(run.start_time),
                    "message": run.state.message[:600],
                }
            )
    return {"since": since.isoformat(), "states": states, "problems": problems[:20]}


def post(text: str, owner: UUID) -> None:
    emit_event(
        event="fastmcp.triage.outcome",
        # The Slack automation (fastmcp-triage-outcome) matches fastmcp.triage.intake.*
        resource={"prefect.resource.id": f"fastmcp.triage.intake.digest-{owner}"},
        payload={"text": text},
    )
    with get_client(sync_client=True) as client:
        client.create_artifact(
            ArtifactCreate(
                key=POSTED_KEY,
                flow_run_id=owner,
                type="fastmcp-triage-digest",
                data={"text": text, "at": datetime.now(UTC).isoformat()},
            )
        )


def digest_plan(raw: object) -> AgentPlan | None:
    value = raw.get("digest") if isinstance(raw, dict) else None
    return AgentPlan.model_validate(value) if isinstance(value, dict) else None


@flow(name="fastmcp-triage-digest", timeout_seconds=900)
def digest(mode: Literal["morning", "check"] = "morning") -> dict:
    logger = get_run_logger()
    plan = digest_plan(Variable.get(CONFIG_VARIABLE, default={}))
    if plan is None:
        logger.info("No digest plan configured; nothing to do")
        return {"mode": mode, "posted": False, "why": "no digest plan configured"}
    context = FlowRunContext.get()
    assert context is not None and context.flow_run is not None
    owner = context.flow_run.id
    now = datetime.now(UTC)
    read = GitHub(secret_sync("fastmcp-triage-gh-token"))
    state = {
        "mode": mode,
        "now": now.isoformat(),
        "queue_url": QUEUE_URL,
        "queue": [queue_entry(item, now) for item in waiting(read.pages)],
        "closed_contributions": gate_closed(read.request, load_standing(), now),
        "runs": intake_runs(now - timedelta(hours=24)),
        "recent": artifacts(POSTED_KEY, 3),
    }
    result, run_id = judge(
        "digest", plan, {}, state, owner, now + timedelta(minutes=12), f"digest-{owner}"
    )
    logger.info("Digest agent (%s): post=%s; %s", run_id, result["post"], result["why"])
    if result["post"] and result["text"].strip():
        post(result["text"], owner)
    return {"mode": mode, "posted": bool(result["post"]), "why": result["why"]}
