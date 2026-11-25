"""Label assessed issues so they sort into the maintainer's queue.

Triage writes only labels that are cheap to be wrong about:

- the issue's area (`client`, `server`, `auth`, ...), when the assessment named one
  and the issue has none yet;
- `needs:triage` on every issue whose outcome waits on a person: review a linked PR,
  fix a reproduced bug, decide a direction, ask the reporter for something, or
  close it. A person removing it clears the hand-off, and triage never re-adds it.

Triage never starts a closing clock on its own. Asking the reporter for a
reproducer (`needs MRE`, closed after 7 days) and flagging a duplicate
(`potential-duplicate`, closed after 3) are proposals: they become the issue's next
step for the digest, and a maintainer decides. When triage isn't sure, it holds.

Writes go out as the triage GitHub App, never a person, and
`fastmcp_triage_config.github_writes` ("off", "dry_run", "on", or a mapping like
{"labels": "on", "sweep": "dry_run"}; default "dry_run") is read on every run, so
turning writes on or off needs no deploy.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import get_args

from flows.agentic_stack_plans import Area

MRE_LABEL = "needs MRE"
DUPLICATE_LABEL = "potential-duplicate"
TRIAGE_LABEL = "needs:triage"
AREA_LABELS = frozenset(get_args(Area))
CONFIG_VARIABLE = "fastmcp_triage_config"
MODES = ("off", "dry_run", "on")

Live = Callable[[int], dict | None]


def mode(config: object, feature: str = "labels") -> str:
    """`github_writes` is one mode for everything, or a mode per feature."""
    value = config.get("github_writes") if isinstance(config, dict) else None
    if isinstance(value, dict):
        value = value.get(feature)
    return value if value in MODES else "dry_run"


def labels_of(current: dict | None) -> set[str]:
    return {label.get("name") for label in (current or {}).get("labels") or []}


def writable(current: dict | None) -> bool:
    """An open issue (not a PR) that a person opened."""
    return bool(
        current
        and current.get("state") == "open"
        and "pull_request" not in current
        and (current.get("user") or {}).get("type") == "User"
    )


def area_of(item: dict) -> str:
    assessment = item.get("assessment")
    area = item.get("area") or (
        assessment.get("area") if isinstance(assessment, dict) else None
    )
    return area if isinstance(area, str) and area in AREA_LABELS else ""


def waits_on_maintainer(bin_name: str, item: dict) -> bool:
    """Everything except an unchanged item or an operational failure needs a person."""
    return bin_name in {"existing_review", "decisions", "evidence", "pruning"}


def cleared_by_person(events: list[dict]) -> bool:
    """Whether a person took `needs:triage` off the issue.

    That is the person saying the hand-off is done. Triage keeps following the issue
    but never puts the label back; a person can.
    """
    return any(
        event.get("event") == "unlabeled"
        and (event.get("label") or {}).get("name") == TRIAGE_LABEL
        and (event.get("actor") or {}).get("type") == "User"
        for event in events
    )


def triage_labels(
    review: dict, live: Live, cleared: Callable[[int], bool] = lambda number: False
) -> list[dict]:
    """Area and `needs:triage` labels for every assessed issue, without comments."""
    writes: list[dict] = []
    seen: set[int] = set()
    for bin_name, items in (review.get("bins") or {}).items():
        for item in items or []:
            number = item.get("issue_number") if isinstance(item, dict) else None
            if not isinstance(number, int) or number in seen:
                continue
            area = area_of(item)
            waiting = waits_on_maintainer(bin_name, item)
            if not area and not waiting:
                continue
            current = live(number)
            if not writable(current):
                continue
            seen.add(number)
            present = labels_of(current)
            if area and not present & AREA_LABELS:
                writes.append({"issue_number": number, "label": area})
            if (
                waiting
                and not present & {TRIAGE_LABEL, MRE_LABEL, DUPLICATE_LABEL}
                and not cleared(number)
            ):
                writes.append({"issue_number": number, "label": TRIAGE_LABEL})
    return writes


def apply_writes(writes: list[dict], request: Callable[..., object]) -> list[dict]:
    applied = []
    for write in writes:
        number = write["issue_number"]
        request(
            f"repos/PrefectHQ/fastmcp/issues/{number}/labels",
            {"labels": [write["label"]]},
        )
        applied.append(write)
    return applied


def summary(writes: list[dict], current_mode: str) -> str:
    verb = "Applied" if current_mode == "on" else "Would apply"
    lines = [f"# GitHub label writes ({current_mode})", ""]
    if not writes:
        lines.append("Nothing to write.")
    for write in writes:
        lines.append(f"- {verb} `{write['label']}` to #{write['issue_number']}")
    return "\n".join(lines) + "\n"
