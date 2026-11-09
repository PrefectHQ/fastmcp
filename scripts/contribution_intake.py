"""Classify external pull requests for contribution intake.

`classify()` is pure: it maps GitHub facts about a PR to a verdict. The I/O layer
below it gathers those facts with read-only `gh api` calls, either live for one PR
or reconstructed as of the moment the issue-link gate closed a PR (`backtest`).

Nothing here writes to GitHub.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum

REPO = "PrefectHQ/fastmcp"
EXEMPT_LABELS = frozenset({"trusted-contributor", "bypass-issue-check"})
OPEN_LABEL = "prs welcome"
GATE_LABEL = "missing-issue-link"
GATE_ACTOR = "github-actions[bot]"
# The issue-link gate reads at most this many linked issues; so do we.
MAX_ISSUES = 5


class IntakeClass(str, Enum):
    EXEMPT = "exempt"
    NO_LINK = "no_link"
    REPORTER = "reporter"
    ACCEPTED = "accepted"
    ISSUE_RESOLVED = "issue_resolved"
    OWN_DUPLICATE = "own_duplicate"
    CANDIDATE = "candidate"


class Action(str, Enum):
    NONE = "none"
    KEEP_OPEN = "keep_open"
    LABEL_FOR_VERIFICATION = "label_for_verification"
    CLOSE = "close"


ACTIONS = {
    IntakeClass.EXEMPT: Action.NONE,
    IntakeClass.NO_LINK: Action.CLOSE,
    IntakeClass.REPORTER: Action.KEEP_OPEN,
    IntakeClass.ACCEPTED: Action.KEEP_OPEN,
    IntakeClass.ISSUE_RESOLVED: Action.CLOSE,
    IntakeClass.OWN_DUPLICATE: Action.CLOSE,
    IntakeClass.CANDIDATE: Action.LABEL_FOR_VERIFICATION,
}


@dataclass(frozen=True)
class LinkedIssue:
    """A referenced issue number; `exists` is False for 404s and pull requests."""

    number: int
    exists: bool = True
    author: str = ""
    state: str = "open"
    state_reason: str | None = None
    assignees: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class OtherPR:
    """Another open PR by the same author."""

    number: int
    linked_issues: tuple[int, ...]


@dataclass(frozen=True)
class PRFacts:
    number: int
    author: str
    author_is_bot: bool = False
    author_has_write: bool = False
    labels: tuple[str, ...] = ()
    linked_issues: tuple[LinkedIssue, ...] = ()
    author_open_prs: tuple[OtherPR, ...] = ()
    # Whether GitHub would accept assigning the author to the linked issue. It
    # silently drops assignment of users who have not interacted with the issue.
    author_assignable: bool | None = None
    as_of: str | None = None


@dataclass(frozen=True)
class Verdict:
    pr: int
    intake_class: IntakeClass
    action: Action
    reason: str
    issue: int | None = None
    pointer: int | None = None
    author_assignable: bool | None = None
    as_of: str | None = None
    notes: tuple[str, ...] = field(default=())

    def to_json(self) -> dict:
        data = asdict(self)
        data["intake_class"] = self.intake_class.value
        data["action"] = self.action.value
        data["notes"] = list(self.notes)
        return data


def _same(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def classify(facts: PRFacts) -> Verdict:
    """Classify a PR. Precedence: exempt > no_link > reporter > accepted >
    issue_resolved > own_duplicate > candidate."""

    def verdict(cls: IntakeClass, reason: str, **kwargs) -> Verdict:
        return Verdict(
            pr=facts.number,
            intake_class=cls,
            action=ACTIONS[cls],
            reason=reason,
            author_assignable=facts.author_assignable,
            as_of=facts.as_of,
            **kwargs,
        )

    if facts.author_is_bot:
        return verdict(IntakeClass.EXEMPT, "author is a bot")
    if facts.author_has_write:
        return verdict(IntakeClass.EXEMPT, "author has write access")
    exempt = sorted(EXEMPT_LABELS.intersection(label.lower() for label in facts.labels))
    if exempt:
        return verdict(IntakeClass.EXEMPT, f"PR is labelled {exempt[0]}")

    real = [issue for issue in facts.linked_issues if issue.exists]
    if not facts.linked_issues:
        return verdict(IntakeClass.NO_LINK, "no auto-close keyword links an issue")
    if not real:
        return verdict(IntakeClass.NO_LINK, "linked issue does not exist")

    for issue in real:
        if _same(issue.author, facts.author):
            return verdict(
                IntakeClass.REPORTER,
                f"author opened #{issue.number}",
                issue=issue.number,
            )
    for issue in real:
        if any(_same(a, facts.author) for a in issue.assignees):
            return verdict(
                IntakeClass.ACCEPTED,
                f"author is assigned to #{issue.number}",
                issue=issue.number,
            )
        if OPEN_LABEL in (label.lower() for label in issue.labels):
            return verdict(
                IntakeClass.ACCEPTED,
                f"#{issue.number} is labelled {OPEN_LABEL}",
                issue=issue.number,
            )

    if all(i.state == "closed" and i.state_reason == "completed" for i in real):
        return verdict(
            IntakeClass.ISSUE_RESOLVED,
            f"#{real[0].number} is already closed as completed",
            issue=real[0].number,
            pointer=real[0].number,
        )

    numbers = {issue.number for issue in real}
    for other in sorted(facts.author_open_prs, key=lambda o: o.number):
        shared = sorted(numbers.intersection(other.linked_issues))
        if other.number < facts.number and shared:
            return verdict(
                IntakeClass.OWN_DUPLICATE,
                f"author already has #{other.number} open for #{shared[0]}",
                issue=shared[0],
                pointer=other.number,
            )

    open_issue = next((i for i in real if i.state == "open"), real[0])
    notes = ()
    if open_issue.state != "open":
        notes = (
            f"#{open_issue.number} is closed ({open_issue.state_reason or 'unknown'})",
        )
    return verdict(
        IntakeClass.CANDIDATE,
        f"links #{open_issue.number}; author is not assigned",
        issue=open_issue.number,
        notes=notes,
    )


def linked_issue_numbers(body: str | None, repo: str = REPO) -> list[int]:
    """Issue numbers named with an auto-close keyword, as require-issue-link.yml reads them."""
    ref = re.escape(repo)
    pattern = re.compile(
        r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*"
        rf"(?:{ref}#|#|https?://github\.com/{ref}/issues/)(\d+)",
        re.IGNORECASE,
    )
    numbers = dict.fromkeys(int(m[1]) for m in pattern.finditer(body or ""))
    return list(numbers)[:MAX_ISSUES]


# ── GitHub I/O ─────────────────────────────────────────────────────────────


class GitHubReadError(RuntimeError):
    def __init__(self, path: str, status: int | None):
        super().__init__(f"GET {path} failed (HTTP {status or 'unknown'})")
        self.status = status


def github(path: str, *, paginate: bool = False) -> object:
    """Read-only `gh api` call. Returns None for an empty (204) response."""
    command = ["gh", "api", path]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    for attempt in (1, 2, 3):
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=True, timeout=60
            )
        except subprocess.TimeoutExpired:
            status = None
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            match = re.search(r"\(HTTP ([1-5][0-9]{2})\)", stderr)
            status = int(match[1]) if match else None
            retryable = status in {403, 429} or (status or 0) >= 500
            if not retryable or attempt == 3:
                raise GitHubReadError(path, status) from None
        else:
            if not result.stdout.strip():
                return None
            value = json.loads(result.stdout)
            if paginate and isinstance(value, list):
                pages = [p["items"] if isinstance(p, dict) else p for p in value]
                return [item for page in pages for item in page]
            return value
        time.sleep(5 * attempt)
    raise GitHubReadError(path, None)


def exists_check(path: str) -> bool:
    """True for 2xx, False for 404; other errors propagate."""
    try:
        github(path)
    except GitHubReadError as exc:
        if exc.status == 404:
            return False
        raise
    return True


_write_cache: dict[str, bool] = {}


def has_write_access(login: str) -> bool:
    if login not in _write_cache:
        try:
            data = github(f"repos/{REPO}/collaborators/{login}/permission")
        except GitHubReadError as exc:
            if exc.status != 404:
                raise
            _write_cache[login] = False
        else:
            assert isinstance(data, dict)
            _write_cache[login] = data.get("permission") in {
                "admin",
                "maintain",
                "write",
            }
    return _write_cache[login]


def get_issue(number: int) -> dict | None:
    """The issue, or None when it does not exist or is a pull request."""
    try:
        data = github(f"repos/{REPO}/issues/{number}")
    except GitHubReadError as exc:
        if exc.status in {404, 410}:
            return None
        raise
    assert isinstance(data, dict)
    return None if "pull_request" in data else data


def live_issue(number: int) -> LinkedIssue:
    data = get_issue(number)
    if data is None:
        return LinkedIssue(number=number, exists=False)
    return LinkedIssue(
        number=number,
        author=data["user"]["login"],
        state=data["state"],
        state_reason=data.get("state_reason"),
        assignees=tuple(a["login"] for a in data.get("assignees") or []),
        labels=tuple(
            label if isinstance(label, str) else label["name"]
            for label in data.get("labels") or []
        ),
    )


def assignable(issue: LinkedIssue | None, login: str) -> bool | None:
    if issue is None or not issue.exists:
        return None
    return exists_check(f"repos/{REPO}/issues/{issue.number}/assignees/{login}")


def live_facts(number: int) -> PRFacts:
    pr = github(f"repos/{REPO}/pulls/{number}")
    assert isinstance(pr, dict)
    author = pr["user"]["login"]
    is_bot = pr["user"].get("type") == "Bot" or author.endswith("[bot]")
    issues = tuple(live_issue(n) for n in linked_issue_numbers(pr.get("body")))
    open_prs = github(f"repos/{REPO}/pulls?state=open&per_page=100", paginate=True)
    assert isinstance(open_prs, list)
    others = tuple(
        OtherPR(p["number"], tuple(linked_issue_numbers(p.get("body"))))
        for p in open_prs
        if _same(p["user"]["login"], author) and p["number"] != number
    )
    first_real = next((i for i in issues if i.exists), None)
    return PRFacts(
        number=number,
        author=author,
        author_is_bot=is_bot,
        author_has_write=not is_bot and has_write_access(author),
        labels=tuple(label["name"] for label in pr.get("labels") or []),
        linked_issues=issues,
        author_open_prs=others,
        author_assignable=None if is_bot else assignable(first_real, author),
    )


# ── Backtest: facts as of the moment the gate closed a PR ─────────────────


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def timeline(number: int) -> list[dict]:
    events = github(
        f"repos/{REPO}/issues/{number}/timeline?per_page=100", paginate=True
    )
    assert isinstance(events, list)
    return [e for e in events if e.get("created_at")]


def replay(events: list[dict], as_of: datetime) -> tuple[set[str], set[str], str, int]:
    """Assignees, labels, state, and index of the last state event as of `as_of`."""
    assignees: set[str] = set()
    labels: set[str] = set()
    state, last_state_event = "open", -1
    for index, event in enumerate(events):
        if parse_time(event["created_at"]) > as_of:
            break
        kind = event["event"]
        if kind == "assigned" and event.get("assignee"):
            assignees.add(event["assignee"]["login"])
        elif kind == "unassigned" and event.get("assignee"):
            assignees.discard(event["assignee"]["login"])
        elif kind == "labeled" and event.get("label"):
            labels.add(event["label"]["name"])
        elif kind == "unlabeled" and event.get("label"):
            labels.discard(event["label"]["name"])
        elif kind in {"closed", "reopened"}:
            state, last_state_event = ("closed" if kind == "closed" else "open"), index
    return assignees, labels, state, last_state_event


def issue_as_of(number: int, as_of: datetime) -> LinkedIssue:
    data = get_issue(number)
    if data is None or parse_time(data["created_at"]) > as_of:
        return LinkedIssue(number=number, exists=False)
    events = timeline(number)
    assignees, labels, state, last = replay(events, as_of)
    # Timeline close events carry no reason; only the final close matches the
    # issue's current state_reason. An earlier close stays unknown.
    later_states = [
        e for e in events[last + 1 :] if e["event"] in {"closed", "reopened"}
    ]
    reason = (
        data.get("state_reason") if state == "closed" and not later_states else None
    )
    return LinkedIssue(
        number=number,
        author=data["user"]["login"],
        state=state,
        state_reason=reason,
        assignees=tuple(sorted(assignees)),
        labels=tuple(sorted(labels)),
    )


def search_prs(query: str) -> list[dict]:
    items = github(
        f"search/issues?q={query.replace(' ', '+')}&per_page=100", paginate=True
    )
    assert isinstance(items, list)
    return items


@dataclass
class BacktestRow:
    number: int
    author: str
    verdict: Verdict


def gate_time(events: list[dict], since: datetime) -> datetime | None:
    for event in events:
        if (
            event["event"] == "labeled"
            and (event.get("label") or {}).get("name") == GATE_LABEL
            and (event.get("actor") or {}).get("login") == GATE_ACTOR
            and parse_time(event["created_at"]) >= since
        ):
            return parse_time(event["created_at"])
    return None


def backtest(since: datetime) -> list[BacktestRow]:
    # Older PRs are included so an author's earlier PR can be a duplicate target.
    lookback = (since - timedelta(days=30)).date().isoformat()
    pool = {
        p["number"]: p
        for p in search_prs(f"repo:{REPO} is:pr created:>={lookback}")
        if p["user"].get("type") != "Bot"
    }
    since_day = since.date().isoformat()
    candidates = sorted(
        n
        for n, p in pool.items()
        if p["updated_at"] >= since_day and not has_write_access(p["user"]["login"])
    )
    rows: list[BacktestRow] = []
    keeps_open: dict[int, bool] = {}
    for number in candidates:
        pr = pool[number]
        events = timeline(number)
        as_of = gate_time(events, since)
        if as_of is None:
            continue
        author = pr["user"]["login"]
        linked = linked_issue_numbers(pr.get("body"))
        issues = tuple(issue_as_of(n, as_of) for n in linked)
        _, pr_labels, _, _ = replay(events, as_of)
        others = []
        for other in pool.values():
            if not _same(other["user"]["login"], author) or other["number"] >= number:
                continue
            if parse_time(other["created_at"]) > as_of:
                continue
            closed_at = other.get("closed_at")
            # A PR the gate closed would have stayed open under intake if its
            # own verdict keeps it open; any other close before as_of counts.
            open_then = closed_at is None or parse_time(closed_at) > as_of
            open_then = open_then or keeps_open.get(other["number"], False)
            if open_then:
                others.append(
                    OtherPR(
                        other["number"], tuple(linked_issue_numbers(other.get("body")))
                    )
                )
        first_real = next((i for i in issues if i.exists), None)
        facts = PRFacts(
            number=number,
            author=author,
            labels=tuple(sorted(pr_labels)),
            linked_issues=issues,
            author_open_prs=tuple(others),
            author_assignable=assignable(first_real, author),
            as_of=as_of.isoformat(),
        )
        verdict = classify(facts)
        keeps_open[number] = verdict.action is not Action.CLOSE
        rows.append(BacktestRow(number, author, verdict))
    return rows


def render_backtest(rows: list[BacktestRow], since: datetime) -> str:
    lines = [
        f"Contribution intake backtest: PRs the issue-link gate labelled since {since.date()}",
        "",
        f"{'PR':>6}  {'author':<22} {'issue':>6}  {'class':<15} {'action':<22} {'assignable':<10} reason",
    ]
    for row in rows:
        v = row.verdict
        lines.append(
            f"{row.number:>6}  {row.author:<22} {('#' + str(v.issue)) if v.issue else '-':>6}  "
            f"{v.intake_class.value:<15} {v.action.value:<22} {str(v.author_assignable):<10} {v.reason}"
        )
    by_class = Counter(row.verdict.intake_class.value for row in rows)
    by_action = Counter(row.verdict.action.value for row in rows)
    lines += ["", f"Total: {len(rows)}", "", "By class:"]
    lines += [f"  {name:<15} {count}" for name, count in by_class.most_common()]
    lines += ["", "By action:"]
    lines += [f"  {name:<22} {count}" for name, count in by_action.most_common()]
    unassignable = [r.number for r in rows if r.verdict.author_assignable is False]
    lines += [
        "",
        f"Author not assignable to linked issue (assignment would be dropped): {unassignable}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    classify_cmd = sub.add_parser(
        "classify", help="classify one PR and print the verdict as JSON"
    )
    classify_cmd.add_argument("--pr", type=int, required=True)
    backtest_cmd = sub.add_parser(
        "backtest", help="replay intake over PRs the issue-link gate closed"
    )
    backtest_cmd.add_argument(
        "--since", required=True, help="ISO date, e.g. 2026-10-26"
    )
    args = parser.parse_args(argv)

    if args.command == "classify":
        print(json.dumps(classify(live_facts(args.pr)).to_json(), indent=2))
        return 0
    since = parse_time(
        args.since + "T00:00:00Z" if "T" not in args.since else args.since
    )
    print(render_backtest(backtest(since), since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
