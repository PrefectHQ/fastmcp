"""Scan generated release notes for shared human contributor credit.

Usage: uv run scripts/release_contributor_credit.py /tmp/generated-notes.md

Prints attribution candidates as JSON. Reads GitHub metadata without modifying
PRs, issues, commits, or release notes. New-contributor history and opt-outs are
reviewed by the release agent only for the candidates returned here.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

REPO = "PrefectHQ/fastmcp"
AGENT_LOGINS = {"claude", "codex", "copilot", "github-copilot", "openai-codex", "devin"}
AGENT_NAMES = re.compile(
    r"^(?:claude (?:opus|sonnet|haiku)|codex|openai codex|github copilot|copilot|devin)(?:\b|$)",
    re.I,
)
HANDLE = r"@[\w-]+(?:\[bot\])?"
PR_ENTRY = re.compile(
    rf"^(?P<prefix>\* .+) by (?P<authors>{HANDLE}(?:, (?:and )?{HANDLE}| and {HANDLE})*) in "
    r"https://github\.com/" + re.escape(REPO) + r"/pull/(?P<number>\d+)\s*$",
    re.M,
)


def api(*args: str) -> dict[str, Any]:
    result = json.loads(subprocess.check_output(["gh", "api", *args], text=True))
    if result.get("errors"):
        raise ValueError(f"GitHub query failed: {result['errors']}")
    return result


def is_human(actor: dict[str, Any] | None) -> bool:
    if not actor:
        return False
    login = actor["login"].lower()
    return (
        actor.get("__typename") != "Bot"
        and not login.endswith("[bot]")
        and login not in AGENT_LOGINS
    )


def is_agent_author(author: dict[str, Any]) -> bool:
    user = author.get("user")
    return (
        bool(user and not is_human(user))
        or bool(AGENT_NAMES.match(author["name"]))
        or author["email"].lower() in {"noreply@anthropic.com", "noreply@openai.com"}
    )


def fetch_prs(numbers: list[int]) -> dict[int, dict[str, Any]]:
    prs: dict[int, dict[str, Any]] = {}
    for start in range(0, len(numbers), 30):
        fields = "\n".join(
            f"""p{number}: pullRequest(number:{number}) {{
                number author {{ login __typename }}
                closingIssuesReferences(first:100) {{
                    nodes {{ number url author {{ login __typename }} }}
                    pageInfo {{ hasNextPage }}
                }}
                commits(first:100) {{
                    nodes {{ commit {{ authors(first:100) {{
                        nodes {{ name email user {{ login __typename }} }}
                        pageInfo {{ hasNextPage }}
                    }} }} }}
                    pageInfo {{ hasNextPage }}
                }}
                mergeCommit {{ authors(first:100) {{
                    nodes {{ name email user {{ login __typename }} }}
                    pageInfo {{ hasNextPage }}
                }} }}
            }}"""
            for number in numbers[start : start + 30]
        )
        query = (
            'query { repository(owner:"PrefectHQ",name:"fastmcp") {' + fields + "} }"
        )
        result = api("graphql", "-f", f"query={query}")
        for pr in result["data"]["repository"].values():
            if pr is None or pr["mergeCommit"] is None:
                raise ValueError("Release notes reference a missing or unmerged PR")
            if any(
                connection["pageInfo"]["hasNextPage"]
                for connection in (
                    pr["closingIssuesReferences"],
                    pr["mergeCommit"]["authors"],
                    pr["commits"],
                    *(node["commit"]["authors"] for node in pr["commits"]["nodes"]),
                )
            ):
                raise ValueError(
                    f"PR #{pr['number']}: attribution metadata is truncated"
                )
            prs[pr["number"]] = pr
    return prs


def release_prs(notes: str) -> dict[int, dict[str, Any]]:
    entries = list(PR_ENTRY.finditer(notes))
    # Fail visibly if the generated format changes; a silent empty scan loses credit.
    linked = set(
        re.findall(r"https://github\.com/" + re.escape(REPO) + r"/pull/(\d+)", notes)
    )
    parsed = {entry["number"] for entry in entries}
    if linked - parsed:
        raise ValueError(
            f"Cannot parse release entries for PRs: {sorted(linked - parsed)}"
        )
    return fetch_prs(sorted(int(number) for number in parsed))


def commit_authors(pr: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        author
        for node in pr["commits"]["nodes"]
        for author in node["commit"]["authors"]["nodes"]
    ] + pr["mergeCommit"]["authors"]["nodes"]


def note_actors(prs: dict[int, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep GitHub actor types, including plain-handle aliases for app bots."""
    actors = {}
    for pr in prs.values():
        sources = [pr["author"]]
        sources.extend(
            issue["author"] for issue in pr["closingIssuesReferences"]["nodes"]
        )
        sources.extend(author.get("user") for author in commit_authors(pr))
        for actor in sources:
            if actor:
                key = actor["login"].lower()
                actors[key] = actor
                if key.endswith("[bot]"):
                    actors[key.removesuffix("[bot]")] = actor
    return actors


def scan_notes(
    notes: str, prs: dict[int, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    if prs is None:
        prs = release_prs(notes)
    actors = note_actors(prs)
    entries = list(PR_ENTRY.finditer(notes))
    permissions: dict[str, bool] = {}

    def is_maintainer(login: str) -> bool:
        key = login.lower()
        if key not in permissions:
            permission = api(f"repos/{REPO}/collaborators/{login}/permission")[
                "permission"
            ]
            permissions[key] = permission in {"admin", "maintain", "write"}
        return permissions[key]

    candidates = []
    for entry in entries:
        number = int(entry["number"])
        pr = prs[number]
        authors = commit_authors(pr)
        humans = [author for author in authors if not is_agent_author(author)]
        original_credit = {
            mention.lstrip("@").lower(): mention.lstrip("@")
            for mention in re.findall(HANDLE, entry["authors"])
            if is_human(actors.get(mention.lstrip("@").lower()))
        }
        credited = dict(original_credit)
        if is_human(pr["author"]):
            login = pr["author"]["login"]
            credited[login.lower()] = login
        for author in humans:
            if is_human(author.get("user")):
                login = author["user"]["login"]
                if login.lower() not in credited and not is_maintainer(login):
                    credited[login.lower()] = login
        issues = [
            issue
            for issue in pr["closingIssuesReferences"]["nodes"]
            if is_human(issue["author"])
        ]
        missing_reporters = []
        extra_reporters = [
            issue
            for issue in issues
            if issue["author"]["login"].lower() not in credited
        ]
        if extra_reporters and pr["author"]:
            login = pr["author"]["login"]
            if is_maintainer(login):
                for issue in extra_reporters:
                    reporter = issue["author"]["login"]
                    if reporter.lower() not in credited and not is_maintainer(reporter):
                        credited[reporter.lower()] = reporter
                        missing_reporters.append(reporter)
        unmapped = list(
            dict.fromkeys(author["name"] for author in humans if not author.get("user"))
        )
        if (
            set(credited) == set(original_credit)
            and not missing_reporters
            and not unmapped
        ):
            continue
        handles = [f"@{login}" for login in credited.values()]
        shared = (
            " and ".join(handles)
            if len(handles) <= 2
            else ", ".join(handles[:-1]) + ", and " + handles[-1]
        )
        candidates.append(
            {
                "pr": number,
                "credit": list(credited.values()),
                "closing_issues": [
                    {
                        "number": issue["number"],
                        "url": issue["url"],
                        "author": issue["author"]["login"],
                    }
                    for issue in issues
                ],
                "missing_reporter_coauthors": missing_reporters,
                "unmapped_commit_authors": unmapped,
                "suggested_entry": (
                    f"{entry['prefix']} by {shared} in https://github.com/{REPO}/pull/{number}"
                    if handles
                    else None
                ),
            }
        )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notes", type=Path)
    args = parser.parse_args()
    notes = args.notes.read_text()
    prs = release_prs(notes)
    candidates = scan_notes(notes, prs)
    print(json.dumps(summarize_credit(notes, candidates, prs), indent=2))


def summarize_credit(
    notes: str, candidates: list[dict[str, Any]], prs: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """Count unique human contributors and identify first-time history checks.

    GitHub's first-time candidates and supplemental contributors need a history
    check, including prior co-authorship, before finalizing that list and its count.
    """
    actors = note_actors(prs)
    original: dict[str, str] = {}
    for entry in PR_ENTRY.finditer(notes):
        for mention in re.findall(HANDLE, entry["authors"]):
            login = mention.lstrip("@")
            if is_human(actors.get(login.lower())):
                original[login.lower()] = login
    credited = dict(original)
    for candidate in candidates:
        for login in candidate["credit"]:
            credited.setdefault(login.lower(), login)
    first_time: dict[str, str] = {}
    for login in re.findall(
        rf"^\* ({HANDLE}) made their first contribution in ", notes, re.M
    ):
        login = login.lstrip("@")
        if is_human(actors.get(login.lower())):
            first_time[login.lower()] = login
    return {
        "candidates": candidates,
        "contributors": sorted(credited.values(), key=str.lower),
        "total_contributors": len(credited),
        "generated_first_time_contributors": sorted(first_time.values(), key=str.lower),
        "generated_first_time_count": len(first_time),
        "first_time_review": sorted(
            (
                login
                for key, login in credited.items()
                if key not in original or key in first_time
            ),
            key=str.lower,
        ),
    }


if __name__ == "__main__":
    main()
