from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from copy import deepcopy

from flows.agentic_stack import digest

Read = Callable[[str], dict]
REPOSITORY = "repos/PrefectHQ/fastmcp"


def fields(value: dict, names: str) -> dict:
    return {name: value[name] for name in names.split() if name in value}


def discussion(values: list[dict]) -> list[dict]:
    result = []
    for value in values:
        item = fields(
            value,
            "id body html_url created_at updated_at submitted_at state commit_id "
            "original_commit_id path line original_line in_reply_to_id author_association",
        )
        item["author"] = fields(value.get("user", {}), "login type")
        result.append(item)
    return result


def events(values: list[dict]) -> list[dict]:
    result = []
    for value in values:
        item = fields(value, "id event created_at commit_id commit_url")
        item["actor"] = fields(value.get("actor") or {}, "login type")
        for name in ("label", "assignee"):
            if value.get(name):
                item[name] = fields(value[name], "name login")
        source = (value.get("source") or {}).get("issue")
        if source:
            item["source"] = fields(source, "number title state html_url")
        result.append(item)
    return result


def checks(pr: dict) -> dict:
    counts = Counter(
        check.get("conclusion") or check.get("status", "unknown")
        for check in pr.get("checks", [])
    )
    latest = {}
    for status in pr.get("statuses", []):
        key = status["context"]
        if key not in latest or status["created_at"] > latest[key]["created_at"]:
            latest[key] = status
    return {
        "head_sha": pr["head_sha"],
        "check_counts": dict(counts),
        "checks": [
            fields(c, "name status conclusion details_url")
            for c in pr.get("checks", [])
            if c.get("conclusion") not in {"success", "skipped", "neutral"}
        ],
        "statuses": [fields(s, "context state target_url") for s in latest.values()],
        "absent": not counts and not latest,
    }


def comparison(read: Read, base: str, head: str, budget: int) -> dict:
    if not all(re.fullmatch(r"[a-f0-9]{40}", sha) for sha in (base, head)):
        raise ValueError("Comparison requires full immutable commit IDs")
    if base == head:
        return {
            "base_sha": base,
            "head_sha": head,
            "status": "identical",
            "complete": True,
            "files": [],
        }
    value = read(f"{REPOSITORY}/compare/{base}...{head}?per_page=100")
    used = 0
    files = []
    omitted = []
    for file in value.get("files", []):
        item = fields(file, "filename previous_filename status additions deletions")
        patch = file.get("patch")
        size = len(patch.encode()) if isinstance(patch, str) else 0
        if patch is not None and used + size <= budget:
            item["patch"] = patch
            used += size
        else:
            item["patch_unavailable"] = (
                "budget" if patch is not None else "not_returned"
            )
            omitted.append(file["filename"])
        files.append(item)
    commits = value.get("commits", [])
    result = {
        "base_sha": base,
        "head_sha": head,
        "status": value["status"],
        "ahead_by": value.get("ahead_by"),
        "behind_by": value.get("behind_by"),
        "commits": [
            {"sha": c["sha"], "message": c["commit"]["message"]} for c in commits
        ],
        "files": files,
        "omitted_patches": omitted,
        "complete": not omitted
        and len(files) < 300
        and value.get("total_commits") == len(commits),
        "tests_executed": False,
    }
    if len(json.dumps(result).encode()) > budget + 4096:
        return {
            **fields(result, "base_sha head_sha status ahead_by behind_by"),
            "complete": False,
            "details_omitted": "comparison_exceeds_context_budget",
            "comparison_url": f"https://github.com/PrefectHQ/fastmcp/compare/{base}...{head}",
            "total_commits": value.get("total_commits"),
            "returned_file_count": len(files),
            "tests_executed": False,
        }
    return result


def revisions(pr: dict, read: Read) -> dict:
    head = pr["head_sha"]
    current = comparison(read, pr["base_sha"], head, 24000)
    reviews = sorted(
        pr.get("reviews", []), key=lambda r: r.get("submitted_at") or "", reverse=True
    )
    references = []
    for review in [*reviews, *pr.get("review_comments", [])]:
        sha = review.get("original_commit_id") or review.get("commit_id")
        if sha and sha not in references:
            references.append(sha)
    compared = [comparison(read, sha, head, 12000) for sha in references[:4]]
    latest = read(f"{REPOSITORY}/pulls/{pr['number']}")
    if latest["head"]["sha"] != head or latest["updated_at"] != pr["updated_at"]:
        raise ValueError("Contribution changed while collecting revision evidence")
    return {
        "current_diff": current,
        "changes_since_review": compared,
        "uncompared_review_commits": references[4:],
        "interpretation": "A review concerns its original commit. Later changes require inspecting these diffs; unanswered does not mean unfixed. Diffs and commit messages are not executed tests.",
    }


def assessment_context(item: dict, read: Read) -> dict:
    raw = item["evidence"]
    if raw.get("complete") is not True:
        raise ValueError("Assessment requires complete retained intake")
    evidence = fields(raw, "schema_version complete base_sha issue reactions stacks")
    evidence = deepcopy(evidence)
    evidence["comments"] = discussion(raw.get("comments", []))
    evidence["timeline"] = events(raw.get("timeline", []))
    evidence["prs"] = []
    if len(raw.get("prs", [])) > 6:
        raise ValueError("More than six contributions need a narrower review batch")
    for pr in raw.get("prs", []):
        result = fields(
            pr,
            "number title body state draft merged html_url labels base_ref base_sha head_sha head_repository",
        )
        result["author"] = fields(pr.get("user", {}), "login type")
        result["comments"] = discussion(pr.get("comments", []))
        result["reviews"] = discussion(pr.get("reviews", []))
        result["review_comments"] = discussion(pr.get("review_comments", []))
        result["timeline"] = events(pr.get("timeline", []))
        result["ci"] = checks(pr)
        if not pr.get("merged", False):
            result["revision_evidence"] = revisions(pr, read)
        evidence["prs"].append(result)
    projected = {
        "evidence": evidence,
        "route": item["route"],
        "group_rationale": item["group_rationale"],
        "retained_intake_sha256": digest(raw),
        "context_policy": "Full discussion text is preserved. Timelines contain event and source identities rather than repeated issue bodies; successful checks are counted. The complete original intake is retained separately. Revision evidence covers open and closed unmerged contributions; closure does not supersede current-head review. Missing or bounded patches are explicit. Verification work belongs to agents, not maintainer questions.",
    }
    if len(json.dumps(projected).encode()) > 160000:
        raise ValueError("Assessment context exceeds 160 KB; narrow the investigation")
    return projected
