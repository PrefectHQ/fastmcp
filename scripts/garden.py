#!/usr/bin/env python
"""
Daily gardening report for FastMCP: what changed since the last release, and
whether what we've shipped still works.

    uv run scripts/garden.py <out-dir> [--skip-published] [--skip-ci]

Writes `garden-report.json` (schema `fastmcp-garden/1`) and `GARDEN.md` into
the output directory. Each section is independent and records its own
errors, so one broken check never hides the others.

- published: install the latest stable and latest prerelease from PyPI, plus
  wheels built from this checkout when `--main-wheels` points at them, each
  in an isolated environment, and run `scripts/garden_smoke.py` there.
- unreleased: main against the latest tag and the latest stable tag. Public
  API breaks (griffe), dependency changes, files that grew, raised `loq`
  caps, and new skips, xfails, type ignores and TODOs.
- ci: failed jobs on main over the last week, grouped by job, and the latest
  nightly upgrade-check and downstream-smoke runs.

The report states facts. Deciding what they mean is the reader's job.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "scripts" / "garden_smoke.py"
REPO = "PrefectHQ/fastmcp"
SOURCE = "fastmcp_slim/fastmcp"
MARKERS = {
    "skip": re.compile(r"pytest\.mark\.skip|pytest\.skip\("),
    "xfail": re.compile(r"pytest\.mark\.xfail|pytest\.xfail\("),
    "type ignore": re.compile(r"#\s*type:\s*ignore|#\s*ty:\s*ignore"),
    "todo": re.compile(r"#\s*(TODO|FIXME|XXX|HACK)\b"),
}
NIGHTLIES = ["run-upgrade-checks.yml", "run-downstream-smoke.yml"]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def sh(*args: str, cwd: Path = ROOT, timeout: int = 600) -> str:
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout
    ).stdout


def section(name: str, build) -> dict:
    """Run one section, keeping its error instead of raising."""
    start = time.perf_counter()
    try:
        body = build()
    except Exception as exc:
        body = {"error": f"{type(exc).__name__}: {exc}"[:2000]}
    body["seconds"] = round(time.perf_counter() - start, 1)
    print(f"{name}: {body['seconds']}s", file=sys.stderr)
    return body


# --- published ---------------------------------------------------------------


def pypi_versions(package: str) -> tuple[str, str | None]:
    """The latest stable version and the latest prerelease newer than it."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json") as resp:
        data = json.load(resp)
    return release_targets(data)


def release_targets(data: dict) -> tuple[str, str | None]:
    stable = data["info"]["version"]
    uploaded = {
        version: min(f["upload_time_iso_8601"] for f in files)
        for version, files in data["releases"].items()
        if files and not all(f.get("yanked") for f in files)
    }
    # Newest prerelease published after the stable release. Upload order, not
    # version order, so a 3.x patch shipped after an alpha doesn't hide it.
    prereleases = [
        version
        for version in uploaded
        if re.search(r"(a|b|rc)\d+$", version)
        and uploaded[version] > uploaded.get(stable, "")
    ]
    return stable, max(prereleases, key=uploaded.__getitem__, default=None)


def smoke(label: str, requirements: list[str]) -> dict:
    uv = shutil.which("uv") or "uv"
    args = [uv, "run", "--isolated", "--no-project", "--no-config"]
    args += ["--prerelease=allow", "--python", "3.12"]
    for requirement in requirements:
        args += ["--with", requirement]
    start = time.perf_counter()
    result = subprocess.run(
        [*args, "python", str(SMOKE)],
        capture_output=True,
        text=True,
        timeout=600,
        cwd="/",
    )
    entry: dict = {
        "target": label,
        "requirements": requirements,
        "seconds_with_install": round(time.perf_counter() - start, 1),
    }
    try:
        entry |= json.loads(result.stdout)
    except json.JSONDecodeError:
        entry["error"] = (result.stderr or result.stdout)[-2000:]
    entry["warnings"] = sorted(
        {
            line.split(": ", 1)[1].strip()
            for line in result.stderr.splitlines()
            if "Warning: " in line and ": " in line
        }
    )
    entry["ok"] = result.returncode == 0 and "error" not in entry
    return entry


def published(main_wheels: Path | None) -> dict:
    stable, prerelease = pypi_versions("fastmcp")
    targets = [("stable", stable)] + (
        [("prerelease", prerelease)] if prerelease else []
    )
    results = [
        smoke(
            f"{label} {version}",
            [f"fastmcp[tasks]=={version}", f"fastmcp-remote=={version}"],
        )
        for label, version in targets
    ]
    if main_wheels:
        wheels = sorted(str(path) for path in main_wheels.glob("*.whl"))
        results.append(smoke("main (built)", wheels))
    return {"stable": stable, "prerelease": prerelease, "targets": results}


# --- unreleased ----------------------------------------------------------------


def tags() -> tuple[str, str]:
    """The latest 4.x tag, and the latest 4.x tag that isn't a prerelease."""
    names = sh("git", "tag", "--list", "v4*", "--sort=-creatordate").split()
    latest = names[0]
    stable = next(name for name in names if not re.search(r"(a|b|rc)\d+$", name))
    return latest, stable


def api_breaks(base: str) -> list[dict]:
    import griffe

    old = griffe.load_git(
        "fastmcp", ref=base, repo=str(ROOT), search_paths=["fastmcp_slim"]
    )
    new = griffe.load("fastmcp", search_paths=[str(ROOT / "fastmcp_slim")])
    return [
        {
            "kind": breakage.kind.value,
            "object": breakage.obj.path,
            "detail": ANSI.sub(
                "", breakage.explain(style=griffe.ExplanationStyle.ONE_LINE)
            ).split(": ", 2)[-1],
        }
        for breakage in griffe.find_breaking_changes(old, new)
        if not any(part.startswith("_") for part in breakage.obj.path.split("."))
    ]


def dependencies(ref: str) -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {}
    for path in ["fastmcp_slim/pyproject.toml", "fastmcp_tasks/pyproject.toml"]:
        project = tomllib.loads(sh("git", "show", f"{ref}:{path}"))["project"]
        deps[f"{path.split('/')[0]}"] = sorted(project.get("dependencies", []))
        for extra, items in project.get("optional-dependencies", {}).items():
            deps[f"{path.split('/')[0]}[{extra}]"] = sorted(items)
    return deps


def dependency_changes(base: str) -> list[dict]:
    old, new = dependencies(base), dependencies("HEAD")
    changes = []
    for group in sorted(old.keys() | new.keys()):
        added = sorted(set(new.get(group, [])) - set(old.get(group, [])))
        removed = sorted(set(old.get(group, [])) - set(new.get(group, [])))
        if added or removed:
            changes.append({"group": group, "added": added, "removed": removed})
    return changes


def added_lines(base: str) -> dict[str, list[str]]:
    diff = sh("git", "diff", "--unified=0", f"{base}..HEAD", "--", "*.py")
    by_file: dict[str, list[str]] = {}
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            by_file.setdefault(current, []).append(line[1:])
    return by_file


def unreleased_for(base: str) -> dict:
    commits = sh("git", "log", "--oneline", f"{base}..HEAD").splitlines()
    growth = []
    for line in sh("git", "diff", "--numstat", f"{base}..HEAD").splitlines():
        plus, minus, path = line.split("\t", 2)
        if plus != "-":
            growth.append({"path": path, "added": int(plus), "removed": int(minus)})
    by_area = Counter()
    for item in growth:
        area = (
            "source"
            if item["path"].startswith(SOURCE)
            else "tests"
            if item["path"].startswith("tests/")
            else "docs"
            if item["path"].startswith("docs/")
            else "other"
        )
        by_area[area] += item["added"] - item["removed"]

    markers = []
    for path, lines in added_lines(base).items():
        for name, pattern in MARKERS.items():
            hits = [line.strip() for line in lines if pattern.search(line)]
            if hits:
                markers.append({"path": path, "marker": name, "lines": hits[:5]})

    old_caps = loq_caps(sh("git", "show", f"{base}:loq.toml"))
    new_caps = loq_caps((ROOT / "loq.toml").read_text())
    raised = [
        {"path": path, "from": old_caps.get(path), "to": cap}
        for path, cap in sorted(new_caps.items())
        if cap > old_caps.get(path, 0)
    ]

    return {
        "base": base,
        "commits": len(commits),
        "net_lines_by_area": dict(by_area),
        "largest_growth": sorted(
            (g for g in growth if g["path"].startswith(SOURCE)),
            key=lambda g: g["added"] - g["removed"],
            reverse=True,
        )[:10],
        "api_breaks": api_breaks(base),
        "dependency_changes": dependency_changes(base),
        "new_markers": markers,
        "loq_caps_raised": raised,
    }


def loq_caps(text: str) -> dict[str, int]:
    return {
        rule["path"]: rule["max_lines"]
        for rule in tomllib.loads(text).get("rules", [])
        if "*" not in rule["path"]
    }


def unreleased() -> dict:
    latest, stable = tags()
    bases = [latest] if latest == stable else [latest, stable]
    return {
        "since": [section(base, lambda b=base: unreleased_for(b)) for base in bases]
    }


# --- ci ----------------------------------------------------------------------


def gh_json(*args: str):
    return json.loads(sh("gh", *args, timeout=120))


def ci() -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    runs = gh_json(
        "run", "list", "--repo", REPO, "--workflow", "run-tests.yml",
        "--branch", "main", "--limit", "100", "--created", f">={since}",
        "--json", "databaseId,conclusion,headSha,createdAt,url",
    )  # fmt: skip
    failed_runs = [run for run in runs if run["conclusion"] == "failure"]
    jobs: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for run in failed_runs:
        for job in gh_json(
            "run", "view", str(run["databaseId"]), "--repo", REPO, "--json", "jobs"
        )["jobs"]:
            if job["conclusion"] == "failure":
                jobs[job["name"]] += 1
                examples.setdefault(job["name"], job["url"])
    nightlies = []
    for workflow in NIGHTLIES:
        latest = gh_json(
            "run", "list", "--repo", REPO, "--workflow", workflow,
            "--event", "schedule", "--limit", "1",
            "--json", "conclusion,createdAt,url",
        )  # fmt: skip
        nightlies.append({"workflow": workflow, **(latest[0] if latest else {})})
    return {
        "since": since,
        "main_runs": len(runs),
        "main_failures": len(failed_runs),
        "failing_jobs": [
            {"job": name, "failures": count, "example": examples[name]}
            for name, count in jobs.most_common()
        ],
        "nightlies": nightlies,
    }


# --- output --------------------------------------------------------------------


def markdown(report: dict) -> str:
    lines = [f"# Garden report, {report['generated_at'][:16]}Z", ""]

    pub = report["published"]
    lines += ["## Published packages", ""]
    if pub.get("skipped"):
        lines += ["Skipped.", ""]
    if "error" in pub:
        lines += [f"Could not run: {pub['error']}", ""]
    for target in pub.get("targets", []):
        mark = "✅" if target["ok"] else "❌"
        smoke_seconds = target.get("seconds", "?")
        lines.append(
            f"- {mark} **{target['target']}**: smoke {smoke_seconds}s,"
            f" {target['seconds_with_install']}s with install"
        )
        for check in target.get("checks", []):
            if check["outcome"] != "pass":
                lines.append(f"  - {check['check']}: {check['detail']}")
        if "error" in target:
            lines.append(f"  - error: `{target['error'][-300:]}`")
        for warning in target.get("warnings", []):
            lines.append(f"  - warning: {warning}")
    lines.append("")

    for since in report["unreleased"].get("since", []):
        lines += [f"## Unreleased since {since.get('base', '?')}", ""]
        if "error" in since:
            lines += [f"Could not run: {since['error']}", ""]
            continue
        net = ", ".join(f"{k} {v:+}" for k, v in since["net_lines_by_area"].items())
        lines += [f"{since['commits']} commits. Net lines: {net}.", ""]
        breaks = since["api_breaks"]
        lines.append(f"- Public API breaks: {len(breaks)}")
        lines += [f"  - `{b['object']}`: {b['detail']}" for b in breaks[:20]]
        for change in since["dependency_changes"]:
            lines.append(
                f"- Dependencies `{change['group']}`: "
                f"+{change['added'] or '[]'} -{change['removed'] or '[]'}"
            )
        for cap in since["loq_caps_raised"]:
            lines.append(
                f"- loq cap raised: `{cap['path']}` {cap['from']} → {cap['to']}"
            )
        counts = Counter(m["marker"] for m in since["new_markers"])
        if counts:
            lines.append(
                "- New markers: "
                + ", ".join(f"{k} in {v} files" for k, v in counts.items())
            )
        top = since["largest_growth"][:5]
        if top:
            lines.append("- Largest source growth:")
            lines += [f"  - `{g['path']}` +{g['added']} -{g['removed']}" for g in top]
        lines.append("")

    ci_section = report["ci"]
    lines += ["## CI on main", ""]
    if "error" in ci_section:
        lines += [f"Could not run: {ci_section['error']}", ""]
    elif not ci_section.get("skipped"):
        lines.append(
            f"{ci_section['main_failures']} of {ci_section['main_runs']} test runs"
            f" failed since {ci_section['since']}."
        )
        for job in ci_section["failing_jobs"]:
            lines.append(
                f"- {job['job']}: {job['failures']} ([example]({job['example']}))"
            )
        for nightly in ci_section["nightlies"]:
            lines.append(
                f"- Last scheduled `{nightly['workflow']}`:"
                f" {nightly.get('conclusion') or 'none'} {nightly.get('url', '')}"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--main-wheels", type=Path)
    parser.add_argument("--skip-published", action="store_true")
    parser.add_argument("--skip-ci", action="store_true")
    args = parser.parse_args()

    report = {
        "schema": "fastmcp-garden/1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "head": sh("git", "rev-parse", "HEAD").strip(),
        "published": {"skipped": True}
        if args.skip_published
        else section("published", lambda: published(args.main_wheels)),
        "unreleased": section("unreleased", unreleased),
        "ci": {"skipped": True} if args.skip_ci else section("ci", ci),
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "garden-report.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / "GARDEN.md").write_text(markdown(report))


if __name__ == "__main__":
    main()
