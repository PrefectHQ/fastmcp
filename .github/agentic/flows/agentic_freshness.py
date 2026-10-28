from __future__ import annotations

import subprocess
from collections.abc import Callable
from copy import deepcopy

import httpx

from flows.agentic_stack_policy import publication_revision


def change_kind(original: dict, current: dict) -> str:
    if publication_revision(original) == publication_revision(current):
        return "unchanged"
    pinned = {**current, "base_sha": original["base_sha"]}
    if publication_revision(original) == publication_revision(pinned):
        return "baseline_changed"
    return "stale"


def revalidate(
    result: dict,
    originals: dict[int, dict],
    branches: dict[int, str],
    read: Callable[[int, str], dict],
) -> dict:
    refreshed = deepcopy(result)
    for number, branch in branches.items():
        outcomes = [
            (name, item)
            for name, items in refreshed["bins"].items()
            if name != "operational"
            for item in items
            if item.get("issue_number") == number
        ]
        if not outcomes:
            continue
        original = originals.get(number)
        status = "stale"
        reason = "Evidence changed or its original snapshot is unavailable; refresh before acting."
        try:
            current = read(number, branch) if original is not None else None
            if original is not None:
                if not isinstance(current, dict):
                    raise ValueError("Invalid fresh evidence")
                status = change_kind(original, current)
                if status == "unchanged":
                    continue
                if status == "baseline_changed":
                    reason = "Release baseline advanced; retain completed work for baseline review and verification."
        except (
            ValueError,
            RuntimeError,
            TimeoutError,
            subprocess.SubprocessError,
            httpx.HTTPError,
            OSError,
        ) as error:
            status = "refresh_failed"
            reason = f"Freshness check failed; retained outcomes are historical: {str(error)[:300]}"
        for name, item in outcomes:
            refreshed["bins"][name].remove(item)
        refreshed["bins"].setdefault("operational", []).append(
            {
                "issue_number": number,
                "status": status,
                "reason": reason,
                "retained_outcomes": [
                    {"bin": name, "result": item} for name, item in outcomes
                ],
            }
        )
    return refreshed
