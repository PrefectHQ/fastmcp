"""The Slack summary of a finished intake: one line per issue that needs a maintainer."""

from __future__ import annotations

import re

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


def action(name: str, item: dict) -> str:
    if name == "existing_review":
        numbers = pull_requests(item)[:3]
        if not numbers:
            return "review the linked contribution"
        label = "PR" if len(numbers) == 1 else "PRs"
        return f"review {label} " + ", ".join(f"#{number}" for number in numbers)
    if name == "pruning":
        return "close? " + clip(first_sentence(item.get("reason")), 100)
    if name == "evidence":
        return "needs repro info"
    return clip(item.get("question") or first_sentence(item.get("reason")), 100)


def outcome_lines(review: dict, issues: list[dict]) -> list[str]:
    known = {issue["issue_number"]: issue for issue in issues}
    lines = []
    for name in ATTENTION_BINS:
        for item in review["bins"].get(name, []):
            number = item.get("issue_number")
            if not isinstance(number, int):
                continue
            subject = known.get(number, {})
            text = ""
            if subject.get("title"):
                text += " " + clip(subject["title"], 70)
            if subject.get("author"):
                text += f" · @{subject['author']}"
            text += " → " + action(name, item)
            lines.append(f"<{ISSUE_URL}{number}|#{number}>" + mrkdwn(text))
    if len(lines) > MAX_LINES:
        hidden = len(lines) - (MAX_LINES - 1)
        lines = lines[: MAX_LINES - 1] + [f"…and {hidden} more in the review bins"]
    return lines
