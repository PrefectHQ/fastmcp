"""A daily look for mistakes the triage automation made on GitHub. Reports only.

Triage holds whatever it isn't sure of, but a wrong write still costs a reporter
their issue, and until now a wrong call was found only when someone happened to
look. This flow gathers what automation did on GitHub lately (labels that start a
closing clock, and items it closed), the automated writes a person later undid, and
every open item whose clock is still running. It hands them to the `audit` agent
role, which judges each against the issue itself and writes a short report. The
flow posts the report to Slack. Nothing here writes to GitHub.

The agent plan comes from `fastmcp_triage_config.audit` ({"flow_id", "version_id"});
without it the flow does nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

from prefect import flow, get_run_logger
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.context import FlowRunContext
from prefect.events import emit_event
from prefect.variables import Variable

from flows.agentic_github import GitHub
from flows.agentic_labels import CONFIG_VARIABLE, DUPLICATE_LABEL, MRE_LABEL
from flows.agentic_stack_runtime import AgentPlan, judge, secret_sync
from flows.agentic_sweep import SWEEP_LABEL

REPOSITORY = "PrefectHQ/fastmcp"
# Labels that close an item on a timer unless someone steps in.
CLOCK_LABELS = (MRE_LABEL, DUPLICATE_LABEL, SWEEP_LABEL)
REPORT_KEY = "fastmcp-triage-audit"
MAX_ITEMS = 30
MAX_EVENT_PAGES = 10

Request = Callable[[str], object]


def automated(actor: dict | None) -> bool:
    return bool(actor) and (
        actor.get("type") == "Bot" or str(actor.get("login", "")).endswith("[bot]")
    )


def recent_events(request: Request, since: datetime) -> list[dict]:
    """Repository issue events newer than `since`, newest first."""
    events: list[dict] = []
    for page in range(1, MAX_EVENT_PAGES + 1):
        batch = request(f"repos/{REPOSITORY}/issues/events?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Expected a list of issue events")
        for event in batch:
            if parse_time(event["created_at"]) < since:
                return events
            events.append(event)
        if len(batch) < 100:
            break
    return events


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_issue(event: dict) -> bool:
    return "pull_request" not in (event.get("issue") or {})


def label_name(event: dict) -> str:
    return (event.get("label") or {}).get("name") or ""


def automated_writes(events: list[dict]) -> list[dict]:
    """Clock labels and closures automation applied to issues."""
    writes = []
    for event in events:
        if not is_issue(event) or not automated(event.get("actor")):
            continue
        if event.get("event") == "labeled" and label_name(event) in CLOCK_LABELS:
            write = f"added {label_name(event)}"
        elif event.get("event") == "closed":
            write = "closed"
        else:
            continue
        writes.append(
            {
                "number": event["issue"]["number"],
                "write": write,
                "by": event["actor"]["login"],
                "at": event["created_at"],
            }
        )
    return writes


def reversals(events: list[dict], history: Callable[[int], list[dict]]) -> list[dict]:
    """Automated writes a person undid: a clock label removed, a closed issue reopened."""
    found = []
    for event in events:
        if not is_issue(event) or automated(event.get("actor")):
            continue
        kind = event.get("event")
        if kind == "unlabeled" and label_name(event) in CLOCK_LABELS:
            undone = ("labeled", label_name(event))
        elif kind == "reopened":
            undone = ("closed", "")
        else:
            continue
        number = event["issue"]["number"]
        earlier = [
            prior
            for prior in history(number)
            if prior.get("event") == undone[0]
            and label_name(prior) == undone[1]
            and parse_time(prior["created_at"]) < parse_time(event["created_at"])
        ]
        if earlier and automated(earlier[-1].get("actor")):
            found.append(
                {
                    "number": number,
                    "write": "added " + undone[1] if undone[1] else "closed",
                    "by": earlier[-1]["actor"]["login"],
                    "undone_by": event["actor"]["login"],
                    "at": event["created_at"],
                }
            )
    return found


def clocks(pages: Callable[[str], list[dict]]) -> list[dict]:
    """Open issues whose closing clock is running."""
    found: dict[int, dict] = {}
    for label in CLOCK_LABELS:
        for item in pages(
            f"repos/{REPOSITORY}/issues?state=open&labels={quote(label)}"
        ):
            if "pull_request" not in item:
                found.setdefault(
                    item["number"], {"number": item["number"], "labels": []}
                )
                found[item["number"]]["labels"].append(label)
    return sorted(found.values(), key=lambda item: item["number"])


def subject(request: Request, number: int) -> dict:
    """What the auditor needs to judge an item: the issue and its recent human talk."""
    issue = request(f"repos/{REPOSITORY}/issues/{number}")
    if not isinstance(issue, dict):
        return {"number": number, "unavailable": True}
    comments = request(f"repos/{REPOSITORY}/issues/{number}/comments?per_page=100")
    human = [
        {
            "author": (comment.get("user") or {}).get("login"),
            "association": comment.get("author_association"),
            "body": (comment.get("body") or "")[:600],
        }
        for comment in (comments if isinstance(comments, list) else [])
        if not automated(comment.get("user"))
    ]
    return {
        "number": number,
        "title": issue.get("title"),
        "state": issue.get("state"),
        "author": (issue.get("user") or {}).get("login"),
        "author_association": issue.get("author_association"),
        "labels": sorted(label.get("name") for label in issue.get("labels") or []),
        "created_at": issue.get("created_at"),
        "body": (issue.get("body") or "")[:2000],
        "comments": human[-5:],
    }


def gather(read: GitHub, now: datetime, hours: int) -> dict:
    events = recent_events(read.request, now - timedelta(hours=hours))

    def history(number: int) -> list[dict]:
        return read.pages(f"repos/{REPOSITORY}/issues/{number}/events")

    writes = automated_writes(events)
    undone = reversals(events, history)
    running = clocks(read.pages)
    numbers = list(
        dict.fromkeys(
            [item["number"] for item in undone]
            + [item["number"] for item in running]
            + [item["number"] for item in writes]
        )
    )
    return {
        "since": (now - timedelta(hours=hours)).isoformat(),
        "writes": writes,
        "reversals": undone,
        "clocks": running,
        "items": [subject(read.request, number) for number in numbers[:MAX_ITEMS]],
        "items_left_out": max(0, len(numbers) - MAX_ITEMS),
    }


def post(text: str, owner: UUID) -> None:
    emit_event(
        event="fastmcp.triage.outcome",
        # The Slack automation (fastmcp-triage-outcome) matches fastmcp.triage.intake.*
        resource={"prefect.resource.id": f"fastmcp.triage.intake.audit-{owner}"},
        payload={"text": text},
    )


def retain(report: dict, owner: UUID) -> None:
    with get_client(sync_client=True) as client:
        client.create_artifact(
            ArtifactCreate(
                key=REPORT_KEY,
                flow_run_id=owner,
                type="fastmcp-triage-audit",
                data=report,
            )
        )


def audit_plan(raw: object) -> AgentPlan | None:
    value = raw.get("audit") if isinstance(raw, dict) else None
    return AgentPlan.model_validate(value) if isinstance(value, dict) else None


@flow(name="fastmcp-triage-audit", timeout_seconds=900)
def audit(hours: int = 26) -> dict:
    logger = get_run_logger()
    plan = audit_plan(Variable.get(CONFIG_VARIABLE, default={}))
    if plan is None:
        logger.info("No audit plan configured; nothing to do")
        return {"posted": False, "why": "no audit plan configured"}
    context = FlowRunContext.get()
    assert context is not None and context.flow_run is not None
    owner = context.flow_run.id
    now = datetime.now(UTC)
    read = GitHub(secret_sync("fastmcp-triage-gh-token"))
    state: dict = {"now": now.isoformat(), **gather(read, now, hours)}
    result, run_id = judge(
        "audit", plan, {}, state, owner, now + timedelta(minutes=12), f"audit-{owner}"
    )
    logger.info(
        "Audit agent (%s): %d mistakes, post=%s; %s",
        run_id,
        len(result["mistakes"]),
        result["post"],
        result["why"],
    )
    retain(
        {
            "at": now.isoformat(),
            "mistakes": result["mistakes"],
            "reversals": state["reversals"],
            "why": result["why"],
        },
        owner,
    )
    if result["post"] and result["text"].strip():
        post(result["text"], owner)
    return {
        "posted": bool(result["post"]),
        "mistakes": [mistake["number"] for mistake in result["mistakes"]],
        "reversals": [item["number"] for item in state["reversals"]],
        "why": result["why"],
    }
