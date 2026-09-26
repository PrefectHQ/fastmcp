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
Public maintenance status for FastMCP.

Writes `status.json` (schema `fastmcp-maintenance/1`), `STATUS.md`, and a
shields.io `badge.json` into the output directory. Automation health comes
from each workflow's own GitHub Actions runs, so every claim links to public
evidence. The contributor queue is published as counts only; per-PR verdicts
stay local (`uv run scripts/gate_digest.py`).

Entries published by other systems are merged in from OPERATOR_STATUS_URL
when it is set, each keeping its own `as_of`.

Requires `gh` authenticated with read access, and TYPESAFE_API_KEY.
"""

import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gate_digest  # noqa: E402

REPO = "PrefectHQ/fastmcp"
SCHEMA = "fastmcp-maintenance/1"
WINDOW_DAYS = 7
STATUS_PAGE = f"https://github.com/{REPO}/blob/status/STATUS.md"

# What each GitHub automation does, checked against its workflow's triggers.
AUTOMATIONS = [
    {
        "id": "issue-link-gate",
        "name": "issue-link gate",
        "what": "holds an external PR until its author is assigned to an issue it links; assignment reopens a gate-closed PR",
        "cadence": "on each external PR change and each issue assignment",
        "workflows": ["require-issue-link.yml"],
    },
    {
        "id": "label-triage",
        "name": "labeling",
        "what": "labels each new issue and PR, and flags submissions that don't follow CONTRIBUTING.md",
        "cadence": "on each new issue or PR",
        "workflows": ["marvin-label-triage.yml"],
    },
    {
        "id": "dedupe",
        "name": "duplicate detection",
        "what": "points out when a new issue duplicates an existing one",
        "cadence": "on each new issue",
        "workflows": ["marvin-dedupe-issues.yml"],
    },
    {
        "id": "auto-close",
        "name": "auto-close",
        "what": "closes issues marked as duplicates, and issues still missing a reproducible example after 7 days of author inactivity",
        "cadence": "daily",
        "workflows": ["auto-close-duplicates.yml", "auto-close-needs-mre.yml"],
    },
    {
        "id": "bug-investigation",
        "name": "bug investigation",
        "what": "investigates new bug reports filed by maintainers and posts findings",
        "cadence": "on each qualifying new issue",
        "workflows": ["marvin-triage-issue.yml"],
    },
    {
        "id": "ci-failure-analysis",
        "name": "CI failure analysis",
        "what": "explains failed test and static-analysis runs on pull requests",
        "cadence": "after each failed PR run",
        "workflows": ["marvin-test-failure.yml"],
    },
    {
        "id": "maintainer-commands",
        "name": "maintainer commands",
        "what": "`/marvin` on an issue or PR asks the bot to act; `/tidy` hides resolved review threads",
        "cadence": "on a maintainer's comment",
        "workflows": [
            "marvin-comment-on-issue.yml",
            "marvin-comment-on-pr.yml",
            "minimize-resolved-reviews.yml",
        ],
    },
    {
        "id": "upgrade-checks",
        "name": "upgrade checks",
        "what": "runs the test suite against the newest dependency releases and opens an issue when it fails",
        "cadence": "nightly",
        "workflows": ["run-upgrade-checks.yml"],
    },
    {
        "id": "release-publish",
        "name": "release publishing",
        "what": "publishes fastmcp-slim, then fastmcp-tasks, fastmcp-remote, and fastmcp to PyPI when a release is cut",
        "cadence": "on each release",
        "workflows": [
            "publish-fastmcp-slim.yml",
            "publish-fastmcp-tasks.yml",
            "publish-fastmcp-remote.yml",
            "publish-fastmcp.yml",
        ],
    },
    {
        "id": "docs-deploy",
        "name": "docs deploy",
        "what": "deploys gofastmcp.com when the published docs change",
        "cadence": "on each docs publication",
        "workflows": ["deploy-docs.yml"],
    },
]

JUDGMENT = [
    "whether to assign an external contributor, which reopens their gated PR",
    "merging, and marking agent-opened draft PRs ready",
    "changes to documented behavior or compatibility, and any API addition",
    "cutting a release, and its title and notes",
    "classifying and disclosing security reports",
]


def gh_json(*args: str):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def workflow_health(files: list[str], since: date) -> dict:
    states, runs = [], []
    for f in files:
        wf = gh_json("api", f"repos/{REPO}/actions/workflows/{f}")
        states.append(wf["state"])
        runs += gh_json(
            "api",
            "-X",
            "GET",
            f"repos/{REPO}/actions/workflows/{f}/runs",
            "-f",
            f"created=>={since.isoformat()}",
            "-f",
            "per_page=100",
            "--jq",
            "[.workflow_runs[] | {conclusion, created_at}]",
        )
    done = [r for r in runs if r["conclusion"] not in (None, "skipped", "cancelled")]
    done.sort(key=lambda r: r["created_at"])
    ok = [r for r in done if r["conclusion"] == "success"]
    if all(s != "active" for s in states):
        state = "off"
    elif not done:
        state = "idle"
    elif done[-1]["conclusion"] != "success":
        state = "degraded"
    else:
        state = "ok"
    tally = Counter(
        "succeeded" if r["conclusion"] == "success" else "failed" for r in done
    )
    return {
        "state": state,
        "last_ok_day": ok[-1]["created_at"][:10] if ok else None,
        "counts": {"succeeded": tally["succeeded"], "failed": tally["failed"]},
    }


def contributor_queue() -> dict:
    rows = gate_digest.gather()
    asyncio.run(gate_digest.judge(rows))
    verdicts = Counter(gate_digest.decide(r)[0] for r in rows)
    return {
        "id": "contributor-queue",
        "name": "contributor queue digest",
        "what": "sorts external PRs waiting on the issue-link gate into worth assigning, needs a human, or decline; only the counts are published",
        "cadence": "twice daily",
        "runs_on": "github-actions",
        "state": "ok",
        "last_ok_day": date.today().isoformat(),
        "evidence_url": f"https://github.com/{REPO}/pulls?q=is%3Apr+is%3Aopen+label%3Amissing-issue-link",
        "window": "now",
        "counts": {
            "waiting": len(rows),
            "worth_assigning": verdicts["worth assigning"],
            "needs_a_human": verdicts["needs a human"],
            "decline": verdicts["decline"],
        },
    }


def operator_entries() -> list[dict]:
    url = os.environ.get("OPERATOR_STATUS_URL")
    if not url:
        return []
    with urllib.request.urlopen(url, timeout=10) as resp:
        doc = json.load(resp)
    if doc.get("schema") != SCHEMA:
        return []
    return [
        dict(a, as_of=doc["as_of"], publisher=doc["publisher"])
        for a in doc["automations"]
    ]


def build() -> dict:
    since = date.today() - timedelta(days=WINDOW_DAYS)
    automations = []
    for a in AUTOMATIONS:
        h = workflow_health(a["workflows"], since)
        automations.append(
            {
                "id": a["id"],
                "name": a["name"],
                "what": a["what"],
                "cadence": a["cadence"],
                "runs_on": "github-actions",
                "state": h["state"],
                "last_ok_day": h["last_ok_day"],
                "evidence_url": f"https://github.com/{REPO}/actions/workflows/{a['workflows'][0]}",
                "window": f"{WINDOW_DAYS}d",
                "counts": h["counts"],
            }
        )
    automations.append(contributor_queue())
    return {
        "schema": SCHEMA,
        "publisher": "fastmcp-actions",
        "as_of": datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "automations": automations + operator_entries(),
        "needs_judgment": JUDGMENT,
        "docs": {
            "maintaining": f"https://github.com/{REPO}/blob/main/MAINTAINING.md",
            "agents": f"https://github.com/{REPO}/blob/main/AGENTS.md",
        },
    }


def overall(doc: dict) -> str:
    mine = [a["state"] for a in doc["automations"] if a["runs_on"] == "github-actions"]
    return "degraded" if "degraded" in mine else "ok"


def markdown(doc: dict) -> str:
    lines = [
        "# FastMCP maintenance status",
        "",
        f"As of {doc['as_of']}. Generated twice a day by "
        f"[maintenance-status](https://github.com/{REPO}/actions/workflows/maintenance-status.yml); "
        "machine-readable as [status.json](status.json). "
        f"How the project is run: [MAINTAINING.md]({doc['docs']['maintaining']}).",
        "",
        "| automation | state | last ok | runs on | cadence |",
        "|---|---|---|---|---|",
    ]
    for a in doc["automations"]:
        name = (
            f"[{a['name']}]({a['evidence_url']})"
            if a.get("evidence_url")
            else a["name"]
        )
        lines.append(
            f"| {name} | {a['state']} | {a['last_ok_day'] or '—'} | {a['runs_on']} | {a['cadence']} |"
        )
    queue = next(a for a in doc["automations"] if a["id"] == "contributor-queue")[
        "counts"
    ]
    lines += [
        "",
        f"**Contributor queue:** {queue['waiting']} PRs waiting on assignment: "
        f"{queue['worth_assigning']} worth assigning, {queue['needs_a_human']} need a human, "
        f"{queue['decline']} decline.",
        "",
        "**Needs a maintainer's judgment:**",
        "",
        *[f"- {j}" for j in doc["needs_judgment"]],
        "",
        "States: `ok` last run succeeded; `idle` nothing to do in the window; "
        "`degraded` last run failed; `off` disabled.",
    ]
    return "\n".join(lines) + "\n"


def badge(doc: dict) -> dict:
    state = overall(doc)
    return {
        "schemaVersion": 1,
        "label": "maintenance",
        "message": state,
        "color": "brightgreen" if state == "ok" else "orange",
    }


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "status-out")
    out.mkdir(parents=True, exist_ok=True)
    doc = build()
    (out / "status.json").write_text(json.dumps(doc, indent=2) + "\n")
    (out / "STATUS.md").write_text(markdown(doc))
    (out / "badge.json").write_text(json.dumps(badge(doc)) + "\n")
    print(markdown(doc))


if __name__ == "__main__":
    main()
