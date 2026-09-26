#!/usr/bin/env python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "vibecheck-py==0.1.1",
# ]
#
# [tool.uv]
# exclude-newer-package = { vibecheck-py = false, typesafe-sdk = false }
# ///
"""
Digest of contributor PRs waiting on the issue-link gate.

Code gathers the hard facts, Jev (through vibecheck) answers four yes/no
questions about each PR, and explicit policy sorts every PR into "worth
assigning", "needs a human", or "decline". Run it locally for the per-PR
report; the maintenance-status workflow publishes only the counts, since the
verdicts are judgments about contributors' work. It never comments, assigns,
or labels.

Requires `gh` authenticated with read access, and TYPESAFE_API_KEY.
"""

import asyncio
import json
import re
import subprocess
import time
from datetime import date, timedelta

import vibecheck

REPO = "PrefectHQ/fastmcp"
# The issue-link gate's parser (.github/workflows/require-issue-link.yml): an
# auto-close keyword before `#N`, `PrefectHQ/fastmcp#N`, or the issue URL.
LINK = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*"
    r"(?:PrefectHQ/fastmcp#|#|https?://github\.com/PrefectHQ/fastmcp/issues/)(\d+)",
    re.IGNORECASE,
)
MAX_LINKS = 5
# The repository's hard-stop markers, checked in the title, the labels, and at
# the start of body lines (so a mention of another PR's `[DNM]` doesn't count).
# "Draft" counts only as `[DRAFT]` or a leading `DRAFT:`, so a title like
# "Draft-4 bounds" isn't held.
DNM = re.compile(
    r"\s*(?:\[?DNM\]?\b|\[?do[\s_-]*not[\s_-]*merge|\[?don'?t[\s_-]*merge|\[draft\]|draft\s*:)",
    re.IGNORECASE,
)
# Checks about the gate or the labeling bot, not about the PR's code.
NON_CODE_CHECKS = {
    "check-issue-link",
    "require-issue-link",
    "reopen-on-assignment",
    "label-issue-or-pr",
}
HIGH_VOLUME = 90
# Act only on clear answers; everything between goes to a person.
DECLINE_BELOW = 0.2
CLEAR_YES = 0.7
CAUSE_BELOW = 0.6
NOISY_AT = 0.6

QUESTIONS = {
    "issue_is_bug": (
        "Does `issue` describe FastMCP behaving incorrectly against behavior it "
        "documents or clearly intends, shown by a reproducible example? Answer "
        "no for questions, feature requests, design proposals, or a user's own "
        "configuration mistake."
    ),
    "scoped_bugfix": (
        "Is `pr` a focused fix for the bug in `issue`, changing only what that "
        "bug needs? Answer no if it adds a feature or option, integrates a "
        "third-party service, refactors unrelated code, or bundles other changes."
    ),
    "fixes_at_cause": (
        "Judging from `pr.description` and `pr.diff`, does the change modify the "
        "code where the problem in `issue` originates? Answer no if it adds a "
        "workaround, special case, or compensation somewhere else and leaves the "
        "originating code path unchanged."
    ),
    "generated_noise": (
        "Does `pr.description` read like unedited generated text: long sections "
        "restating the diff, speculative analysis, lists of alternative "
        "approaches, or test summaries, instead of a short statement of the "
        "problem and the fix?"
    ),
}


def gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    ).stdout


def gh_json(*args: str):
    return json.loads(gh(*args))


def search_count(query: str) -> int:
    # The search API allows 30 requests a minute.
    time.sleep(2.1)
    return gh_json(
        "api", "-X", "GET", "search/issues", "-f", f"q={query}", "-f", "per_page=1"
    )["total_count"]


def linked_issues(body: str) -> list[str]:
    found: list[str] = []
    for n in LINK.findall(body or ""):
        if n not in found:
            found.append(n)
    return found[:MAX_LINKS]


def other_prs(issue: str, pr: int, author: str) -> dict[str, list[int]]:
    """Other contributions that link `issue`, whether or not the gate holds them.

    Open PRs and PRs the gate closed still compete: assignment reopens a
    gate-closed PR. A merged one may already fix the issue. The author's own
    earlier attempts, and PRs a maintainer closed on the merits, are left out.
    """
    hits = gh_json(
        "pr",
        "list",
        "--repo",
        REPO,
        "--state",
        "all",
        "--limit",
        "20",
        "--search",
        f"{issue} in:body",
        "--json",
        "number,body,state,author,labels",
    )
    found: dict[str, list[int]] = {"competing": [], "merged": []}
    for h in hits:
        if h["number"] == pr or h["author"]["login"] == author:
            continue
        if issue not in linked_issues(h["body"]):
            continue
        gated = "missing-issue-link" in {lb["name"] for lb in h["labels"]}
        if h["state"] == "MERGED":
            found["merged"].append(h["number"])
        elif h["state"] == "OPEN" or gated:
            found["competing"].append(h["number"])
    return {k: sorted(v) for k, v in found.items()}


def fetch_issue(n: str, pr: int, author: str) -> dict:
    iss = gh_json(
        "issue",
        "view",
        n,
        "--repo",
        REPO,
        "--json",
        "title,body,author,state,assignees,labels,comments",
    )
    return {
        "number": int(n),
        "title": iss["title"],
        "body": (iss["body"] or "")[:3000],
        "labels": [lb["name"] for lb in iss["labels"]],
        "comments": [
            {"author": c["author"]["login"], "body": c["body"][:600]}
            for c in iss["comments"][-5:]
        ],
        "reporter": iss["author"]["login"],
        "state": iss["state"],
        "assignees": [a["login"] for a in iss["assignees"]],
        **other_prs(n, pr, author),
    }


def pick_issue(issues: list[dict], author: str) -> dict | None:
    """The linked issue this PR can be assigned on, as the gate would take it."""

    def eligible(i: dict) -> bool:
        return i["state"] == "OPEN" and (not i["assignees"] or author in i["assignees"])

    candidates = [i for i in issues if eligible(i)]
    if not candidates:
        return None
    return max(candidates, key=lambda i: (i["reporter"] == author, not i["competing"]))


def gather() -> list[dict]:
    prs = gh_json(
        "pr",
        "list",
        "--repo",
        REPO,
        "--state",
        "open",
        "--label",
        "missing-issue-link",
        "--limit",
        "100",
        "--json",
        "number,title,author,createdAt,body,additions,deletions,"
        "statusCheckRollup,files,isDraft,labels",
    )
    since = (date.today() - timedelta(days=30)).isoformat()
    authors: dict[str, dict] = {}
    rows = []
    for p in prs:
        author = p["author"]["login"]
        if author not in authors:
            authors[author] = {
                "merged_here": search_count(
                    f"repo:{REPO} type:pr is:merged author:{author}"
                ),
                "prs_30d": search_count(f"type:pr author:{author} created:>={since}"),
            }
        failing = sorted(
            {
                c.get("name") or c.get("context") or "?"
                for c in p["statusCheckRollup"]
                if (c.get("conclusion") or c.get("state")) in {"FAILURE", "ERROR"}
            }
            - NON_CODE_CHECKS
        )
        labels = [lb["name"] for lb in p["labels"]]
        issues = [fetch_issue(n, p["number"], author) for n in linked_issues(p["body"])]
        rows.append(
            {
                "pr": p["number"],
                "title": p["title"],
                "author": author,
                "opened": p["createdAt"][:10],
                "size": p["additions"] + p["deletions"],
                "files": [f["path"] for f in p["files"]],
                "failing_checks": failing,
                "hold": p["isDraft"]
                or any(DNM.search(t) for t in [p["title"], *labels])
                or any(DNM.match(line) for line in (p["body"] or "").splitlines()),
                "linked": issues,
                "issue": pick_issue(issues, author),
                "description": (p["body"] or "")[:3000],
                "diff": gh("pr", "diff", str(p["number"]), "--repo", REPO)[:6000],
                **authors[author],
            }
        )
    return rows


async def judge(rows: list[dict]) -> None:
    sem = asyncio.Semaphore(4)

    async def one(r: dict) -> None:
        if r["issue"] is None:
            return
        data = {
            "issue": {
                k: r["issue"][k] for k in ("title", "body", "labels", "comments")
            },
            "pr": {
                "title": r["title"],
                "description": r["description"],
                "files": r["files"],
                "diff": r["diff"],
            },
        }
        async with sem, vibecheck.batch(data) as b:
            pending = {k: b.check(q, probabilities=True) for k, q in QUESTIONS.items()}
        r["jev"] = {k: round(d.result().probabilities, 2) for k, d in pending.items()}

    await asyncio.gather(*(one(r) for r in rows))


def decide(r: dict) -> tuple[str, list[str]]:
    # Decline only on facts; a low score goes to a person, since a maintainer
    # may have approved an enhancement or clarified the contract.
    if not r["linked"]:
        return "decline", ["no linked issue"]
    iss = r["issue"]
    if iss is None:
        closed = [f"#{i['number']}" for i in r["linked"] if i["state"] != "OPEN"]
        taken = [
            f"#{i['number']} (assigned to {', '.join(i['assignees'])})"
            for i in r["linked"]
            if i["state"] == "OPEN"
        ]
        return "decline", [
            "every linked issue is closed or assigned elsewhere: "
            + ", ".join(closed + taken)
        ]
    if r["hold"]:
        return "needs a human", ["draft or marked do-not-merge"]

    j = r.get("jev", {})
    reporter = iss["reporter"] == r["author"]
    if iss["competing"] and not reporter:
        rivals = ", ".join(f"#{x}" for x in iss["competing"])
        return "needs a human", [
            f"#{iss['number']} has other contributions ({rivals}); pick one"
        ]

    reasons = []
    if iss["merged"]:
        merged = ", ".join(f"#{x}" for x in iss["merged"])
        reasons.append(f"{merged} already merged for #{iss['number']}; may be fixed")
    if iss["competing"]:
        reasons.append(
            "reporter's PR; others on the issue: "
            + ", ".join(f"#{x}" for x in iss["competing"])
        )
    if r["failing_checks"]:
        reasons.append("failing: " + ", ".join(r["failing_checks"]))
    if j["issue_is_bug"] < DECLINE_BELOW:
        reasons.append(
            f"issue reads as not a bug ({j['issue_is_bug']}); check for an approved design"
        )
    elif j["issue_is_bug"] < CLEAR_YES:
        reasons.append(f"bug unclear ({j['issue_is_bug']})")
    if j["scoped_bugfix"] < DECLINE_BELOW:
        reasons.append(
            f"reads as an enhancement ({j['scoped_bugfix']}); needs an approved design"
        )
    elif j["scoped_bugfix"] < CLEAR_YES:
        reasons.append(f"scope unclear ({j['scoped_bugfix']})")
    if j["fixes_at_cause"] < CAUSE_BELOW:
        reasons.append(f"may patch a symptom ({j['fixes_at_cause']})")
    if j["generated_noise"] >= NOISY_AT:
        reasons.append(f"description reads generated ({j['generated_noise']})")
    if not any(f.startswith("tests/") for f in r["files"]):
        reasons.append("adds no test")
    if r["prs_30d"] >= HIGH_VOLUME:
        reasons.append(
            f"author opened {r['prs_30d']} PRs in 30 days; check follow-through"
        )
    if r["size"] > 400:
        reasons.append(f"large ({r['size']} lines)")
    if reasons:
        return "needs a human", reasons

    why = ["reporter" if reporter else "only PR on the issue", "checks green"]
    if r["merged_here"]:
        why.append(f"{r['merged_here']} merged here before")
    return "worth assigning", why


def report(rows: list[dict]) -> str:
    order = ["worth assigning", "needs a human", "decline"]
    lines = [
        "## Gate queue digest",
        "",
        f"{len(rows)} contributor PRs are waiting on assignment. Nothing was assigned or posted.",
        "",
    ]
    for verdict in order:
        group = sorted(
            (r for r in rows if r["verdict"] == verdict), key=lambda r: r["opened"]
        )
        lines += [
            f"### {verdict} ({len(group)})",
            "",
            "| PR | issue | author | why | bug · scoped · cause · noise |",
            "|---|---|---|---|---|",
        ]
        for r in group:
            j = r.get("jev", {})
            scores = " · ".join(f"{j[k]:.2f}" for k in QUESTIONS) if j else ""
            issue = f"#{r['issue']['number']}" if r["issue"] else ""
            title = r["title"].replace("|", "\\|")
            lines.append(
                f"| #{r['pr']} {title} | {issue} | @{r['author']} | {'; '.join(r['reasons'])} | {scores} |"
            )
        lines.append("")
    lines.append(
        "Scores are Jev's probability of yes to: the issue shows a bug; the PR is a "
        "scoped fix; it changes the code where the problem starts; the description "
        'reads like unedited generated text. "Worth assigning" means worth a '
        "maintainer's review, not ready to merge."
    )
    return "\n".join(lines)


def main() -> None:
    rows = gather()
    asyncio.run(judge(rows))
    for r in rows:
        r["verdict"], r["reasons"] = decide(r)
    print(report(rows))


if __name__ == "__main__":
    main()
