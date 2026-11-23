"""Turn a finished intake into the GitHub labels that the repository's closers act on.

Triage used to stop at Slack. Two outcomes now become GitHub state:

- A reviewed "needs a reproduction" outcome gets the `needs MRE` label and a fixed
  request. `auto-close-needs-mre.yml` closes it after 7 days without the author.
- A reviewed `close_duplicate` recommendation gets a "possible duplicate" comment and
  the `potential-duplicate` label. `auto-close-duplicates.yml` closes it after its
  3-day cooldown unless someone objects.

Every assessed issue also gets labels that need no follow-up:

- its area (`client`, `server`, `auth`, ...), when the assessment named one and the
  issue has none yet;
- `needs:triage` when the outcome waits on a maintainer: review a linked PR, prepare a
  fix for a reproduced bug, decide a direction, or close for a reason other than a
  duplicate.

Nothing here closes an issue. Writes go out as the triage GitHub App, never a person,
and `fastmcp_triage_config.github_writes` ("off", "dry_run", "on", or a mapping
like {"labels": "on", "sweep": "dry_run"}; default "dry_run") is read on every run,
so turning writes on or off needs no deploy.
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

MRE_COMMENT = (
    "Thanks for the report. We couldn't reproduce this from the description alone. "
    "Could you add a minimal, self-contained script that shows the problem, with the "
    "FastMCP version, what you expected and what happened instead? "
    "See [CONTRIBUTING](https://github.com/PrefectHQ/fastmcp/blob/main/CONTRIBUTING.md).\n\n"
    "This issue closes automatically in 7 days without a reply; a reply or edit keeps it open."
)

# The duplicate closer looks for a Bot comment containing "possible duplicate issue".
DUPLICATE_COMMENT = (
    "Found 1 possible duplicate issue:\n\n1. #{canonical}\n\n{detail}"
    "This issue will be closed as a duplicate in 3 days unless you react with 👎 "
    "or comment here."
)

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


def reviewed(item: dict) -> bool:
    # Policy outcomes (an existing label, a claim) carry no independent review.
    return isinstance(item.get("adversarial_review"), dict)


def duplicate_target(item: dict) -> tuple[int, str] | None:
    number = item.get("issue_number")
    for recommendation in item["adversarial_review"].get("recommendations") or []:
        canonical = recommendation.get("canonical_issue")
        if (
            recommendation.get("action") == "close_duplicate"
            and recommendation.get("target_number") == number
            and isinstance(canonical, int)
            and canonical != number
        ):
            return canonical, str(recommendation.get("public_comment") or "").strip()
    return None


def planned_writes(review: dict, live: Live) -> list[dict]:
    """The label writes a finished intake justifies, checked against live GitHub state."""
    writes: list[dict] = []
    seen: set[int] = set()
    bins = review.get("bins") or {}
    for item in bins.get("evidence") or []:
        number = item.get("issue_number")
        if (
            not isinstance(number, int)
            or number in seen
            or not reviewed(item)
            or item.get("decision") != "evidence_needed"
        ):
            continue
        current = live(number)
        if not writable(current) or labels_of(current) & {MRE_LABEL, DUPLICATE_LABEL}:
            continue
        seen.add(number)
        writes.append(
            {"issue_number": number, "label": MRE_LABEL, "comment": MRE_COMMENT}
        )
    for item in bins.get("pruning") or []:
        number = item.get("issue_number")
        if not isinstance(number, int) or number in seen or not reviewed(item):
            continue
        target = duplicate_target(item)
        if target is None:
            continue
        canonical, detail = target
        current, original = live(number), live(canonical)
        if (
            not writable(current)
            or labels_of(current) & {MRE_LABEL, DUPLICATE_LABEL, "duplicate"}
            or not original
            or "pull_request" in original
        ):
            continue
        seen.add(number)
        writes.append(
            {
                "issue_number": number,
                "label": DUPLICATE_LABEL,
                "comment": DUPLICATE_COMMENT.format(
                    canonical=canonical, detail=detail + "\n\n" if detail else ""
                ),
                "canonical_issue": canonical,
            }
        )
    return writes


def area_of(item: dict) -> str:
    assessment = item.get("assessment")
    area = item.get("area") or (
        assessment.get("area") if isinstance(assessment, dict) else None
    )
    return area if isinstance(area, str) and area in AREA_LABELS else ""


def waits_on_maintainer(bin_name: str, item: dict) -> bool:
    if bin_name in {"existing_review", "decisions"}:
        return True
    if bin_name == "evidence":
        return item.get("decision") == "reproduced"
    if bin_name == "pruning":
        return not (reviewed(item) and duplicate_target(item))
    return False


def triage_labels(review: dict, live: Live) -> list[dict]:
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
            if waiting and not present & {TRIAGE_LABEL, MRE_LABEL, DUPLICATE_LABEL}:
                writes.append({"issue_number": number, "label": TRIAGE_LABEL})
    return writes


def apply_writes(writes: list[dict], request: Callable[..., object]) -> list[dict]:
    """Comment first, then label, so a label never appears without its explanation."""
    applied = []
    for write in writes:
        number = write["issue_number"]
        if write.get("comment"):
            request(
                f"repos/PrefectHQ/fastmcp/issues/{number}/comments",
                {"body": write["comment"]},
            )
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
        extra = (
            f" (duplicate of #{write['canonical_issue']})"
            if write.get("canonical_issue")
            else ""
        )
        lines.append(f"- {verb} `{write['label']}` to #{write['issue_number']}{extra}")
    return "\n".join(lines) + "\n"
