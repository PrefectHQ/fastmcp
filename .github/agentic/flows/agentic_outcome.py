"""The Slack summary of a finished intake: one line per issue that needs a maintainer."""

from __future__ import annotations

import re
from collections.abc import Callable

ISSUE_URL = "https://github.com/PrefectHQ/fastmcp/issues/"
# Bins whose items wait on a maintainer, in the order they are listed. Unchanged
# items need nothing, and operational ones go through the failure alert.
ATTENTION_BINS = ("existing_review", "decisions", "pruning", "evidence")
MAX_LINES = 6


def subjects(originals: dict[int, dict], pending: list[dict]) -> list[dict]:
    """Title and author of each admitted issue, from its collected evidence."""
    titles = {item["issue_number"]: item.get("title") for item in pending}
    found = {
        number: {
            "issue_number": number,
            "title": evidence["issue"].get("title") or titles.get(number),
            "author": (evidence["issue"].get("user") or {}).get("login"),
        }
        for number, evidence in originals.items()
    }
    for number, title in titles.items():
        found.setdefault(
            number, {"issue_number": number, "title": title, "author": None}
        )
    return [found[number] for number in sorted(found)]


def clip(text: object, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def mrkdwn(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def first_sentence(text: object) -> str:
    return re.split(r"(?<=[.!?])\s", " ".join(str(text or "").split()), maxsplit=1)[0]


def pull_requests(item: dict) -> list[int]:
    numbers = [
        check["pr_number"]
        for check in item.get("verification") or []
        if isinstance(check.get("pr_number"), int)
    ]
    numbers = numbers or list(item.get("verification_prs") or [])
    if not numbers:
        linked = (item.get("route") or {}).get("pull_requests") or []
        numbers = [pr["number"] for pr in linked if pr.get("state") == "open"]
    return list(dict.fromkeys(numbers))


# Reads one issue or pull request from the GitHub issues API, or None if unknown.
Live = Callable[[int], dict | None]
GATE_LABEL = "missing-issue-link"


def pr_status(live: Live | None, number: int) -> tuple[str, str | None]:
    """open, merged, gated (closed by the issue-link gate) or closed, plus the author."""
    current = live(number) if live else None
    if not current:
        return "open", None
    author = (current.get("user") or {}).get("login")
    if (current.get("pull_request") or {}).get("merged_at"):
        return "merged", author
    if current.get("state") != "closed":
        return "open", author
    labels = {label.get("name") for label in current.get("labels") or []}
    return ("gated" if GATE_LABEL in labels else "closed"), author


def review_action(item: dict, live: Live | None) -> str:
    numbers = pull_requests(item)[:3]
    if not numbers:
        return "review the linked contribution"
    status = {number: pr_status(live, number) for number in numbers}
    merged = [n for n in numbers if status[n][0] == "merged"]
    if merged:
        return f"PR #{merged[0]} merged: close the issue"
    open_ = [n for n in numbers if status[n][0] == "open"]
    if open_:
        label = "PR" if len(open_) == 1 else "PRs"
        return f"review {label} " + ", ".join(f"#{n}" for n in open_)
    gated = [n for n in numbers if status[n][0] == "gated"]
    if gated:
        number, author = gated[0], status[gated[0]][1]
        who = f"@{author}" if author else "its author"
        return f"PR #{number} is gate-closed: assign {who} to reopen it, or decline"
    return "linked PRs are closed: " + clip(
        first_sentence(item.get("next_step") or "decide on the issue"), 60
    )


def evidence_action(item: dict) -> str:
    decision = item.get("decision")
    detail = item.get("next_step") or item.get("observed") or item.get("reason")
    if decision == "reproduced":
        prefix = "reproduced"
    elif decision == "not_reproduced":
        prefix = "could not reproduce"
    else:
        prefix = "needs repro info"
    if not detail:
        return prefix
    return f"{prefix}: " + clip(first_sentence(detail), 70)


def action(name: str, item: dict, live: Live | None = None) -> str:
    if name == "existing_review":
        return review_action(item, live)
    if name == "pruning":
        return "close? " + clip(first_sentence(item.get("reason")), 60)
    if name == "evidence":
        return evidence_action(item)
    detail = first_sentence(item.get("next_step") or item.get("reason") or "")
    return clip(item.get("question") or detail or "needs a maintainer decision", 60)


def still_open(live: Live | None, number: int) -> bool:
    current = live(number) if live else None
    return not current or current.get("state") != "closed"


def outcome_lines(
    review: dict, issues: list[dict], live: Live | None = None
) -> list[str]:
    """One line per issue still waiting on a maintainer.

    With `live`, each line is checked against current GitHub state first: closed
    issues are dropped, and linked PRs are described as they are now (merged,
    open, or closed by the issue-link gate), not as they were at intake.
    """
    known = {issue["issue_number"]: issue for issue in issues}
    lines = []
    for name in ATTENTION_BINS:
        for item in review["bins"].get(name, []):
            number = item.get("issue_number")
            if not isinstance(number, int) or not still_open(live, number):
                continue
            step = action(name, item, live)
            subject = known.get(number, {})
            text = ""
            if subject.get("title"):
                text += " " + clip(subject["title"], 50)
            if subject.get("author"):
                text += f" · @{subject['author']}"
            text += " → " + step
            lines.append(f"<{ISSUE_URL}{number}|#{number}>" + mrkdwn(text))
    if len(lines) > MAX_LINES:
        hidden = len(lines) - (MAX_LINES - 1)
        lines = lines[: MAX_LINES - 1] + [f"…and {hidden} more in the review bins"]
    return lines
